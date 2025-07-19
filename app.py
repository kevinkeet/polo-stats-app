import os
import json
import uuid
import time
import sys
from datetime import date, datetime
from flask import Flask, render_template, request, redirect, url_for, abort
from flask_sqlalchemy import SQLAlchemy
import google.generativeai as genai
from dotenv import load_dotenv
import speech_recognition as sr
from pydub import AudioSegment

# --- SETUP ---
load_dotenv()
app = Flask(__name__)
app.config['UPLOAD_FOLDER'] = 'uploads'
app.config['DATA_FOLDER'] = 'data' # Used only for initial data seeding
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

# --- DATABASE CONFIGURATION ---
db_url = os.getenv('DATABASE_URL')
if not db_url:
    print("WARNING: DATABASE_URL not found. Defaulting to local sqlite database 'polo_stats.db'")
    db_url = 'sqlite:///polo_stats.db'
if db_url.startswith("postgres://"):
    db_url = db_url.replace("postgres://", "postgresql://", 1)
app.config['SQLALCHEMY_DATABASE_URI'] = db_url
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)

# --- DATABASE MODELS ---
class Team(db.Model):
    id = db.Column(db.String(100), primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    players = db.relationship('Player', backref='team', lazy=True, cascade="all, delete-orphan")
    games = db.relationship('Game', backref='team', lazy=True, cascade="all, delete-orphan")

class Player(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    team_id = db.Column(db.String(100), db.ForeignKey('team.id'), nullable=False)
    cap_number = db.Column(db.Integer)
    picture = db.Column(db.String(255))
    school = db.Column(db.String(100))
    club = db.Column(db.String(100))
    height = db.Column(db.String(20))
    handedness = db.Column(db.String(20))
    position = db.Column(db.String(50))
    favorite_ice_cream = db.Column(db.String(100))

class Game(db.Model):
    id = db.Column(db.String(100), primary_key=True, default=lambda: str(uuid.uuid4()))
    team_id = db.Column(db.String(100), db.ForeignKey('team.id'), nullable=False)
    opponent = db.Column(db.String(100), nullable=False)
    date = db.Column(db.String(50), nullable=False)
    events_list = db.Column(db.JSON)
    formatted_events = db.Column(db.Text)
    game_summary = db.Column(db.Text)
    stats = db.Column(db.JSON)
    raw_transcripts = db.Column(db.JSON) # NEW FIELD

STAT_CATEGORIES = (
    "goal,assist,shot,steal,ejections_drawn,rebound,"
    "turnover,field_block,ejection_committed,save,sprint_won,"
    "5_meter_taken,5_meter_made,5_meter_blocked"
).split(',')

# --- AI CONFIGURATION ---
try:
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise ValueError("GEMINI_API_KEY not found in environment.")
    genai.configure(api_key=api_key)
    model = genai.GenerativeModel('gemini-1.5-flash')
    print("--- AI Model configured successfully. ---")
except Exception as e:
    print(f"!!! FATAL ERROR during AI startup: {e}")
    model = None

# --- HELPER FUNCTIONS ---
def parse_text_with_ai(text, roster_names):
    if not model: 
        print("!!! AI model not available. Skipping parsing.")
        return []
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

def generate_game_summary(events):
    if not model: return "AI model not available."
    if not events: return "No summary available."
    event_log = "\n".join([f"- {event['action']} by {event['player']}" for event in events if isinstance(event, dict)])
    prompt = f"""
    You are a sports journalist. Based on the following log of water polo game events, write a short, engaging, 2-3 sentence summary of the game. 
    Mention one or two standout players if possible.
    Event Log:
    {event_log}
    Summary:
    """
    try:
        response = model.generate_content(prompt)
        return response.text.strip()
    except Exception as e:
        print(f"!!! AI Summary Generation Error: {e}")
        return "Summary could not be generated."

def format_events_for_display(events):
    lines = []
    for event in events:
        if not isinstance(event, dict): continue
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

def calculate_season_stats_from_db(team_id, roster):
    player_names = [player.name for player in roster]
    season_stats = {name: {cat: 0 for cat in STAT_CATEGORIES} for name in player_names}
    games = Game.query.filter_by(team_id=team_id).all()
    for game in games:
        if game.stats:
            for player, stats in game.stats.items():
                if player in season_stats:
                    for stat, value in stats.items():
                        if stat in season_stats[player]:
                            season_stats[player][stat] += value
    return season_stats

def init_db():
    with app.app_context():
        db.create_all()
        print("Initialized the database.")
        
        teams = [d for d in os.listdir('data') if os.path.isdir(os.path.join('data', d))]
        for team_id in teams:
            if not Team.query.get(team_id):
                new_team = Team(id=team_id, name=team_id.replace('_', ' ').title())
                db.session.add(new_team)
                print(f"Added team: {team_id}")
                
                roster_path = os.path.join('data', team_id, 'roster.json')
                if os.path.exists(roster_path):
                    with open(roster_path, 'r') as f:
                        roster_data = json.load(f)
                        for player_info in roster_data:
                            existing_player = Player.query.filter_by(name=player_info['name'], team_id=team_id).first()
                            if not existing_player:
                                new_player = Player(team_id=team_id, **player_info)
                                db.session.add(new_player)
                                print(f"  - Added player: {player_info['name']}")
        db.session.commit()
        print("Database seeded with initial team and roster data.")

# --- FLASK ROUTES ---
@app.context_processor
def inject_version():
    return dict(version=time.time())

@app.route('/')
def landing_page():
    teams = Team.query.order_by(Team.name).all()
    return render_template('landing.html', teams=teams)

@app.route('/team/<team_id>')
def index(team_id):
    team = Team.query.get_or_404(team_id)
    roster = Player.query.filter_by(team_id=team_id).order_by(Player.name).all()
    all_games = Game.query.filter_by(team_id=team_id).order_by(Game.date.desc()).all()
    season_stats = calculate_season_stats_from_db(team_id, roster)
    latest_game = all_games[0] if all_games else None
    today_date = date.today().strftime('%Y-%m-%d')
    return render_template('index.html', team_id=team_id, roster=roster, season_stats=season_stats, games=all_games, latest_game=latest_game, stat_categories=STAT_CATEGORIES, today_date=today_date)

@app.route('/team/<team_id>/upload', methods=['POST'])
def upload_and_process(team_id):
    roster = Player.query.filter_by(team_id=team_id).all()
    if not roster:
        return "Error: Cannot process upload for a team with no roster.", 500
    
    game_text = ""
    if 'commentary_text' in request.form and request.form['commentary_text'].strip():
        game_text = request.form['commentary_text']
    elif 'transcript_file' in request.files and request.files['transcript_file'].filename != '':
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
        print("DEBUG: No game text found in form. Redirecting.")
        return redirect(url_for('index', team_id=team_id))
    
    roster_names = [player.name for player in roster]
    parsed_events = parse_text_with_ai(game_text, roster_names)
    
    valid_events = []
    for event in parsed_events:
        if isinstance(event, dict):
            event['timestamp'] = str(datetime.now())
            valid_events.append(event)
        else:
            print(f"WARNING: Skipping malformed event from AI: {event}")

    game_stats = {name: {cat: 0 for cat in STAT_CATEGORIES} for name in roster_names}
    for event in valid_events:
        player = event.get('player', '').strip()
        action = event.get('action', '').strip()
        if player in game_stats and action in game_stats[player]:
            game_stats[player][action] += 1
            
    new_game = Game(
        id=str(uuid.uuid4()),
        team_id=team_id,
        opponent=request.form.get('opponent', 'Unknown'),
        date=request.form.get('game_date', 'N/A'),
        events_list=valid_events,
        formatted_events=format_events_for_display(valid_events),
        game_summary="Click the button below to generate a summary.",
        stats=game_stats,
        raw_transcripts=[{"timestamp": str(datetime.now()), "text": game_text}] # SAVE RAW TEXT
    )
    db.session.add(new_game)
    db.session.commit()
    return redirect(url_for('index', team_id=team_id))

@app.route('/team/<team_id>/game/<game_id>')
def game_detail(team_id, game_id):
    game = Game.query.get_or_404(game_id)
    roster = Player.query.filter_by(team_id=team_id).all()
    player_names = [player.name for player in roster]
    return render_template('game.html', team_id=team_id, game=game, stat_categories=STAT_CATEGORIES, player_names=player_names)

@app.route('/team/<team_id>/player/<player_name>')
def player_detail(team_id, player_name):
    player = Player.query.filter_by(team_id=team_id, name=player_name).first_or_404()
    roster = Player.query.filter_by(team_id=team_id).all()
    season_stats = calculate_season_stats_from_db(team_id, roster)
    player_stats = season_stats.get(player_name, {cat: 0 for cat in STAT_CATEGORIES})
    return render_template('player.html', team_id=team_id, player=player, stats=player_stats, stat_categories=STAT_CATEGORIES)

@app.route('/team/<team_id>/delete_game/<game_id>', methods=['POST'])
def delete_game(team_id, game_id):
    game = Game.query.get(game_id)
    if game:
        db.session.delete(game)
        db.session.commit()
    return redirect(url_for('index', team_id=team_id))

@app.route('/team/<team_id>/update_game/<game_id>', methods=['POST'])
def update_game(team_id, game_id):
    game = Game.query.get_or_404(game_id)
    roster = Player.query.filter_by(team_id=team_id).all()
    new_text = ""
    if 'commentary_text' in request.form and request.form['commentary_text'].strip():
        new_text = request.form['commentary_text']
    elif 'transcript_file' in request.files and request.files['transcript_file'].filename != '':
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
        # Append new raw transcript
        if game.raw_transcripts is None:
            game.raw_transcripts = []
        game.raw_transcripts = game.raw_transcripts + [{"timestamp": str(datetime.now()), "text": new_text}]

        roster_names = [player.name for player in roster]
        new_events_raw = parse_text_with_ai(new_text, roster_names)
        
        new_events = []
        for event in new_events_raw:
            if isinstance(event, dict):
                event['timestamp'] = str(datetime.now())
                new_events.append(event)
            else:
                print(f"WARNING: Skipping malformed event from AI: {event}")

        all_events = (game.events_list or []) + new_events
        all_events.sort(key=lambda x: x.get('timestamp', ''))
        
        game.events_list = all_events
        game.formatted_events = format_events_for_display(all_events)
        
        game_stats = {name: {cat: 0 for cat in STAT_CATEGORIES} for name in roster_names}
        for event in all_events:
            player = event.get('player', '').strip()
            action = event.get('action', '').strip()
            if player in game_stats and action in game_stats[player]:
                game_stats[player][action] += 1
        game.stats = game_stats
        
        db.session.commit()

    return redirect(url_for('game_detail', team_id=team_id, game_id=game_id))

@app.route('/team/<team_id>/generate_summary/<game_id>', methods=['POST'])
def generate_summary(team_id, game_id):
    game = Game.query.get_or_404(game_id)
    summary = generate_game_summary(game.events_list)
    game.game_summary = summary
    db.session.commit()
    return redirect(url_for('game_detail', team_id=team_id, game_id=game_id))

# --- MAIN EXECUTION AND COMMAND ---
if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] == 'init-db':
        init_db()
        sys.exit()
    
    app.run(debug=True)
