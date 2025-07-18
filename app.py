import os
import json
import uuid
import time
from datetime import date, datetime
from flask import Flask, render_template, request, redirect, url_for, abort
import google.generativeai as genai
from dotenv import load_dotenv
import speech_recognition as sr
from pydub import AudioSegment

# --- SETUP ---
load_dotenv()
app = Flask(__name__)
app.config['UPLOAD_FOLDER'] = 'uploads'
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
os.makedirs('games', exist_ok=True)

# --- UPDATED STATS ---
STAT_CATEGORIES = (
    "goal,assist,shot,steal,ejections_drawn,rebound,"
    "turnover,field_block,ejection_committed,save,sprint_won"
).split(',')
print(f"--- STAT CATEGORIES INITIALIZED: {STAT_CATEGORIES} ---")

try:
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise ValueError("GEMINI_API_KEY not found. Please check your .env file.")
    genai.configure(api_key=api_key)
    model = genai.GenerativeModel('gemini-1.5-flash')
except Exception as e:
    print(f"!!! FATAL ERROR during startup: {e}")
    exit()

# --- HELPER FUNCTIONS ---
def get_roster():
    try:
        with open('roster.json', 'r') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError) as e:
        print(f"!!! ERROR loading roster.json: {e}")
        return []

def get_all_games_data():
    games = []
    for filename in sorted(os.listdir('games'), reverse=True):
        if filename.endswith('.json'):
            try:
                with open(os.path.join('games', filename), 'r') as f:
                    games.append(json.load(f))
            except json.JSONDecodeError:
                print(f"Warning: Could not read corrupted game file: {filename}")
    return games

def calculate_season_stats(games_data, roster):
    player_names = [player['name'] for player in roster]
    season_stats = {name: {cat: 0 for cat in STAT_CATEGORIES} for name in player_names}
    for game in games_data:
        for player, stats in game.get('stats', {}).items():
            if player in season_stats:
                for stat, value in stats.items():
                    if stat in season_stats[player]:
                        season_stats[player][stat] += value
    return season_stats

def parse_text_with_ai(text, roster_names):
    prompt = f"""
    You are a water polo stats assistant. Your task is to analyze game commentary and extract key events.
    The official team roster is: {', '.join(roster_names)}.
    The valid actions to identify are: {', '.join(STAT_CATEGORIES)}.
    Analyze the commentary below. Identify events. For each event, identify the player involved. 
    CRITICAL: You MUST map any nicknames or partial names to the player's FULL, OFFICIAL name from the roster.
    CRITICAL: The action name in your output MUST be one of the valid actions listed above.
    *** IMPORTANT RULE FOR ASSISTS ***
    Only count an 'assist' if a pass is EXPLICITLY MENTIONED as leading directly to a goal. Do not infer assists.
    Return the events as a valid JSON list of objects. Each object must have "player" and "action" keys.
    Game Commentary to Analyze:
    "{text}"
    JSON Output:
    """
    try:
        print("--- Sending text to AI for parsing... ---")
        response = model.generate_content(prompt)
        json_text = response.text.replace('\u00a0', ' ').replace('```json', '').replace('```', '').strip()
        print(f"--- AI Response (cleaned): ---\n{json_text}\n--------------------------")
        return json.loads(json_text)
    except Exception as e:
        print(f"!!! AI Parsing or JSON loading Error: {e}")
        return []

def format_events_for_display(events):
    lines = []
    for event in events:
        player = event.get('player')
        action = event.get('action', '').replace('_', ' ')
        action_formatted = ' '.join(word.capitalize() for word in action.split())
        timestamp_str = event.get('timestamp')
        try:
            dt_obj = datetime.strptime(timestamp_str, '%Y-%m-%d %H:%M:%S.%f')
            ts_formatted = dt_obj.strftime('%b %d, %I:%M %p')
        except (ValueError, TypeError):
            ts_formatted = "No Time"
        lines.append(f"[{ts_formatted}] {action_formatted} by {player}!")
    return "\n".join(reversed(lines))

def transcribe_audio(filepath):
    r = sr.Recognizer()
    with sr.AudioFile(filepath) as source:
        audio = r.record(source)
    try:
        return r.recognize_google(audio)
    except sr.UnknownValueError:
        return "Audio was unclear."
    except sr.RequestError as e:
        return f"Could not request results; {e}"

# --- FLASK ROUTES ---
@app.context_processor
def inject_version():
    return dict(version=time.time())

@app.route('/')
def index():
    roster = get_roster()
    if not roster:
        return "Error: Could not load team roster. Check 'roster.json'."
    all_games = get_all_games_data()
    season_stats = calculate_season_stats(all_games, roster)
    latest_game = all_games[0] if all_games else None
    today_date = date.today().strftime('%Y-%m-%d')
    return render_template('index.html', roster=roster, season_stats=season_stats, games=all_games, latest_game=latest_game, stat_categories=STAT_CATEGORIES, today_date=today_date)

@app.route('/upload', methods=['POST'])
def upload_and_process():
    roster = get_roster()
    if not roster:
        return "Error: Cannot process upload.", 500
    
    game_text = ""
    if 'transcript_file' in request.files and request.files['transcript_file'].filename != '':
        file = request.files['transcript_file']
        game_text = file.read().decode('utf-8')
    elif 'audio_file' in request.files and request.files['audio_file'].filename != '':
        file = request.files['audio_file']
        original_filepath = os.path.join(app.config['UPLOAD_FOLDER'], file.filename)
        file.save(original_filepath)
        try:
            sound = AudioSegment.from_file(original_filepath)
            wav_filename = os.path.splitext(file.filename)[0] + ".wav"
            wav_filepath = os.path.join(app.config['UPLOAD_FOLDER'], wav_filename)
            sound.export(wav_filepath, format="wav")
            game_text = transcribe_audio(wav_filepath)
        except Exception as e:
            print(f"Error converting audio: {e}")
            game_text = "Error processing audio."

    if not game_text:
        return redirect(url_for('index'))
        
    roster_names = [player['name'] for player in roster]
    parsed_events = parse_text_with_ai(game_text, roster_names)
    
    for event in parsed_events:
        event['timestamp'] = str(datetime.now())

    formatted_events_text = format_events_for_display(parsed_events)
    game_stats = {name: {cat: 0 for cat in STAT_CATEGORIES} for name in roster_names}
    for event in parsed_events:
        player = event.get('player', '').strip()
        action = event.get('action', '').strip()
        if player in game_stats and action in game_stats[player]:
            game_stats[player][action] += 1
            
    game_id = str(uuid.uuid4())
    game_data = {
        'id': game_id,
        'opponent': request.form.get('opponent', 'Unknown'),
        'date': request.form.get('game_date', 'N/A'),
        'events_list': parsed_events,
        'formatted_events': formatted_events_text,
        'stats': game_stats
    }
    with open(os.path.join('games', f'{game_id}.json'), 'w') as f:
        json.dump(game_data, f, indent=2)
    return redirect(url_for('index'))

@app.route('/game/<game_id>')
def game_detail(game_id):
    game_filepath = os.path.join('games', f'{game_id}.json')
    try:
        with open(game_filepath, 'r') as f:
            game_data = json.load(f)
    except FileNotFoundError:
        abort(404)
    roster = get_roster()
    player_names = [player['name'] for player in roster]
    return render_template('game.html', game=game_data, stat_categories=STAT_CATEGORIES, player_names=player_names)

@app.route('/player/<player_name>')
def player_detail(player_name):
    roster = get_roster()
    player_data = next((p for p in roster if p['name'] == player_name), None)
    if not player_data:
        abort(404)
    
    all_games = get_all_games_data()
    season_stats = calculate_season_stats(all_games, roster)
    player_stats = season_stats.get(player_name, {cat: 0 for cat in STAT_CATEGORIES})
    
    return render_template('player.html', player=player_data, stats=player_stats, stat_categories=STAT_CATEGORIES)

@app.route('/delete_game/<game_id>', methods=['POST'])
def delete_game(game_id):
    try:
        game_filepath = os.path.join('games', f'{game_id}.json')
        os.remove(game_filepath)
    except FileNotFoundError:
        print(f"Could not find game file: {game_id}.json")
    return redirect(url_for('index'))

@app.route('/update_game/<game_id>', methods=['POST'])
def update_game(game_id):
    game_filepath = os.path.join('games', f'{game_id}.json')
    try:
        with open(game_filepath, 'r') as f:
            game_data = json.load(f)
    except FileNotFoundError:
        abort(404)

    new_text = ""
    if 'transcript_file' in request.files and request.files['transcript_file'].filename != '':
        file = request.files['transcript_file']
        new_text = file.read().decode('utf-8')
    elif 'audio_file' in request.files and request.files['audio_file'].filename != '':
        file = request.files['audio_file']
        original_filepath = os.path.join(app.config['UPLOAD_FOLDER'], file.filename)
        file.save(original_filepath)
        try:
            sound = AudioSegment.from_file(original_filepath)
            wav_filename = os.path.splitext(file.filename)[0] + ".wav"
            wav_filepath = os.path.join(app.config['UPLOAD_FOLDER'], wav_filename)
            sound.export(wav_filepath, format="wav")
            new_text = transcribe_audio(wav_filepath)
        except Exception as e:
            print(f"Error converting audio: {e}")
            new_text = "Error processing audio."

    if new_text:
        roster = get_roster()
        roster_names = [player['name'] for player in roster]
        new_events = parse_text_with_ai(new_text, roster_names)
        
        for event in new_events:
            event['timestamp'] = str(datetime.now())

        all_events = game_data.get('events_list', []) + new_events
        all_events.sort(key=lambda x: x.get('timestamp', ''))
        
        game_data['events_list'] = all_events
        game_data['formatted_events'] = format_events_for_display(all_events)
        
        game_stats = {name: {cat: 0 for cat in STAT_CATEGORIES} for name in roster_names}
        for event in all_events:
            player = event.get('player', '').strip()
            action = event.get('action', '').strip()
            if player in game_stats and action in game_stats[player]:
                game_stats[player][action] += 1
        game_data['stats'] = game_stats
        
        with open(game_filepath, 'w') as f:
            json.dump(game_data, f, indent=2)

    return redirect(url_for('game_detail', game_id=game_id))

# --- MAIN EXECUTION ---
if __name__ == '__main__':
    app.run(debug=True)
