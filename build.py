import os
import json
import shutil
from jinja2 import Environment, FileSystemLoader
# We need to import the functions from your app.py file
from app import get_all_games_data, calculate_season_stats, get_roster, STAT_CATEGORIES

print("--- Starting static site build ---")

# Setup Jinja2 environment to look for templates in the 'templates' folder
env = Environment(loader=FileSystemLoader('templates'))

# Get all the necessary data using functions from your app
roster = get_roster()
all_games = get_all_games_data()
season_stats = calculate_season_stats(all_games, roster)
player_names = [player['name'] for player in roster]

# Create the output directory, clearing it first to ensure a clean build
output_dir = 'dist'
if os.path.exists(output_dir):
    shutil.rmtree(output_dir)
os.makedirs(os.path.join(output_dir, 'game'))
os.makedirs(os.path.join(output_dir, 'player'))

# --- Build index.html (Main Page) ---
print("Building: index.html")
template = env.get_template('index.html')
latest_game = all_games[0] if all_games else None
# Render the template with all the data it needs
html_content = template.render(
    roster=roster,
    season_stats=season_stats,
    games=all_games,
    latest_game=latest_game,
    stat_categories=STAT_CATEGORIES
)
with open(os.path.join(output_dir, 'index.html'), 'w') as f:
    f.write(html_content)

# --- Build Individual Game Pages ---
game_template = env.get_template('game.html')
for game in all_games:
    game_id = game['id']
    print(f"Building: game/{game_id}.html")
    html_content = game_template.render(
        game=game,
        stat_categories=STAT_CATEGORIES,
        player_names=player_names,
        roster=roster # Pass the full roster for player links
    )
    with open(os.path.join(output_dir, 'game', f'{game_id}.html'), 'w') as f:
        f.write(html_content)

# --- Build Individual Player Pages ---
player_template = env.get_template('player.html')
for player in roster:
    player_name = player['name']
    print(f"Building: player/{player_name}.html")
    player_stats = season_stats.get(player_name, {})
    html_content = player_template.render(
        player=player,
        stats=player_stats,
        stat_categories=STAT_CATEGORIES
    )
    # Use a sanitized filename
    safe_player_name = player_name.replace("'", "").replace(" ", "_")
    with open(os.path.join(output_dir, 'player', f'{safe_player_name}.html'), 'w') as f:
        f.write(html_content)

# --- Copy Static Files (CSS) ---
print("Copying static files...")
shutil.copytree('static', os.path.join(output_dir, 'static'))

print("--- Build complete! Your static site is in the 'dist' folder. ---")
