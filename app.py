# app.py
from flask import Flask, render_template, send_from_directory, abort, jsonify, request
import os
import json
from pathlib import Path
import mimetypes
from datetime import datetime, timedelta
import threading
import time
import requests

app = Flask(__name__)

# Configuration
GAMES_FOLDER = 'games'
STATIC_FOLDER = 'static'
PLAY_COUNTS_FILE = 'play_counts.json'

# --- Helper Functions ---

def load_games_dataset():
    try:
        with open('games_dataset.json', 'r') as f:
            dataset = json.load(f)
            return dataset
    except FileNotFoundError:
        print("❌ games_dataset.json not found! Creating empty dataset.")
        return {}
    except json.JSONDecodeError as e:
        print(f"❌ Error parsing games_dataset.json: {e}")
        return {}

def load_play_counts():
    """Load play counts from JSON file"""
    try:
        with open(PLAY_COUNTS_FILE, 'r') as f:
            return json.load(f)
    except FileNotFoundError:
        return {}
    except json.JSONDecodeError:
        return {}

def save_play_counts(play_counts):
    """Save play counts to JSON file"""
    try:
        with open(PLAY_COUNTS_FILE, 'w') as f:
            json.dump(play_counts, f, indent=2)
    except Exception as e:
        print(f"❌ Error saving play counts: {e}")

def increment_play_count(game_id):
    """Increment play count for a specific game"""
    play_counts = load_play_counts()
    
    if game_id not in play_counts:
        play_counts[game_id] = {
            'total_plays': 0,
            'recent_plays': 0,
            'last_played': None,
            'weekly_plays': []
        }
    
    game_data = play_counts[game_id]
    game_data['total_plays'] += 1
    game_data['recent_plays'] += 1
    game_data['last_played'] = datetime.now().isoformat()
    
    # Add to weekly tracking (keep last 7 days)
    today = datetime.now().date().isoformat()
    if not game_data['weekly_plays'] or game_data['weekly_plays'][-1]['date'] != today:
        game_data['weekly_plays'].append({'date': today, 'plays': 1})
    else:
        game_data['weekly_plays'][-1]['plays'] += 1
    
    # Keep only last 7 days
    cutoff_date = (datetime.now() - timedelta(days=7)).date().isoformat()
    game_data['weekly_plays'] = [
        day for day in game_data['weekly_plays'] 
        if day['date'] >= cutoff_date
    ]
    
    save_play_counts(play_counts)
    return game_data

def calculate_dynamic_badge(game_id, play_counts):
    """Calculate badge based on play statistics"""
    if game_id not in play_counts:
        return None
    
    game_data = play_counts[game_id]
    total_plays = game_data['total_plays']
    recent_plays = game_data['recent_plays']
    
    # Calculate weekly plays
    weekly_plays = sum(day['plays'] for day in game_data.get('weekly_plays', []))
    
    # Get average plays across all games for comparison
    all_games_plays = [data['total_plays'] for data in play_counts.values()]
    if not all_games_plays:
        return None
    
    avg_plays = sum(all_games_plays) / len(all_games_plays)
    
    # Badge logic based on relative performance
    if weekly_plays >= 50:  # High weekly activity
        return "hot"
    elif total_plays >= avg_plays * 2:  # Significantly above average
        return "popular"
    elif recent_plays >= 20:  # Good recent activity
        return "trending"
    elif total_plays >= 100:  # High total plays
        return "classic"
    elif game_data.get('last_played'):
        # Check if it's a new game (played recently but not many times)
        last_played = datetime.fromisoformat(game_data['last_played'])
        if (datetime.now() - last_played).days <= 7 and total_plays <= 10:
            return "new"
    
    return None

def get_html_entry_point(game_path):
    if not os.path.isdir(game_path):
        return None
    for file in os.listdir(game_path):
        if file.lower() == 'index.html' or file.lower().endswith('.html'):
            return file
    return None

def detect_game_type(game_path):
    files = os.listdir(game_path)
    if any(file.lower().endswith('.html') for file in files):
        return 'html'
    if any(file.lower().endswith('.swf') for file in files):
        return 'flash'
    return None

def get_games_list():
    if hasattr(get_games_list, 'cached_games'):
        return get_games_list.cached_games

    games_dataset = load_games_dataset()
    play_counts = load_play_counts()
    games_by_section = {}
    
    if not os.path.exists(GAMES_FOLDER):
        os.makedirs(GAMES_FOLDER)
        return games_by_section
    
    items = os.listdir(GAMES_FOLDER)
    
    for game_folder in items:
        game_path = os.path.join(GAMES_FOLDER, game_folder)
        
        if not os.path.isdir(game_path) or not os.path.exists(os.path.join(game_path, 'thumbnail.png')):
            continue
            
        game_info = games_dataset.get(game_folder, {})
        iframe_url = game_info.get('iframe_url')
        
        if iframe_url:
            game_type = 'iframe'
        else:
            game_type = detect_game_type(game_path)

        if not game_type:
            continue
            
        section = game_info.get('section', 'Action games')
        
        if section not in games_by_section:
            games_by_section[section] = []
        
        # Calculate dynamic badge
        dynamic_badge = calculate_dynamic_badge(game_folder, play_counts)
        
        game_data = {
            'id': game_folder,
            'name': game_info.get('name', game_folder.replace('-', ' ').title()),
            'type': game_type,
            'badge': dynamic_badge,  # Use dynamic badge instead of static
            'rating': game_info.get('rating', None),
            'category': game_info.get('category', []),
            'description': game_info.get('description', ''),
            'instructions': game_info.get('instructions', ''),
            'featured': game_info.get('featured', False),
            'iframe_url': iframe_url,
            'play_count': play_counts.get(game_folder, {}).get('total_plays', 0),
            'weekly_plays': sum(day['plays'] for day in play_counts.get(game_folder, {}).get('weekly_plays', []))
        }
        
        if game_type == 'html':
            game_data['entry_point'] = get_html_entry_point(game_path)

        games_by_section[section].append(game_data)
    
    # Sort games by play count (most popular first) within each section
    for section in games_by_section:
        games_by_section[section].sort(key=lambda x: x['play_count'], reverse=True)
    
    get_games_list.cached_games = games_by_section
    return games_by_section

# --- Helper for rendering full pages or partial content ---
def render_or_jsonify(content_template, **context):
    """
    If request is for partial content, returns JSON with rendered HTML and page title.
    Otherwise, renders the full base template, injecting the specified content.
    """
    if request.args.get('partial') == 'true':
        html = render_template(content_template, **context)
        title = context.get('title', 'CraveGames')
        return jsonify(html=html, title=title)
    
    context['content_template'] = content_template
    return render_template('base.html', **context)

# --- Page-serving routes ---
@app.route('/')
def index():
    games_by_section = get_games_list()
    context = {
        'games_by_section': games_by_section,
        'active_page': 'Home',
        'title': 'CraveGames - Home'
    }
    return render_or_jsonify('index_content.html', **context)

@app.route('/game/<game_id>')
def game_page(game_id):
    # Clear cache to get fresh data
    if hasattr(get_games_list, 'cached_games'):
        delattr(get_games_list, 'cached_games')
    
    games_by_section = get_games_list()
    all_games = [game for games in games_by_section.values() for game in games]
    current_game = next((game for game in all_games if game['id'] == game_id), None)
    
    if not current_game:
        abort(404)
    
    # Increment play count when game page is visited
    increment_play_count(game_id)
    
    other_games = [game for game in all_games if game['id'] != game_id][:12]
    
    context = {
        'game': current_game,
        'other_games': other_games,
        'active_page': current_game.get('category', [None])[0],
        'title': f"{current_game['name']} - CraveGames",
        'meta_description': f"Jump into {current_game['name']}—free to play anytime on CraveGames.me. Start craving games today!"
    }
    return render_or_jsonify('game_content.html', **context)


@app.route('/category/<category_name>')
def category_page(category_name):
    games_by_section = get_games_list()
    all_games = [game for games in games_by_section.values() for game in games]
    
    games_in_category = [
        game for game in all_games 
        if category_name.lower() in [c.lower() for c in game.get('category', [])]
    ]
    
    context = {
        'category_name': category_name,
        'games': games_in_category,
        'active_page': category_name,
        'title': f"{category_name} Games - CraveGames"
    }
    return render_or_jsonify('category_content.html', **context)

# --- NEW: Analytics endpoints ---
@app.route('/api/game-stats/<game_id>')
def game_stats(game_id):
    """Get detailed stats for a specific game"""
    play_counts = load_play_counts()
    if game_id not in play_counts:
        return jsonify({'error': 'Game not found'}), 404
    
    return jsonify(play_counts[game_id])

@app.route('/api/top-games')
def top_games():
    """Get top games by play count"""
    play_counts = load_play_counts()
    games_by_section = get_games_list()
    all_games = [game for games in games_by_section.values() for game in games]
    
    # Sort by play count
    top_games = sorted(all_games, key=lambda x: x['play_count'], reverse=True)[:10]
    
    return jsonify(top_games)

# --- Asset and API routes ---
@app.route('/ads.txt')
def ads_txt():
    return send_from_directory(app.root_path, 'ads.txt', mimetype='text/plain')

@app.route('/play/<game_id>')
def play_game(game_id):
    # Increment play count when game is actually played
    increment_play_count(game_id)
    
    game_path = os.path.join(GAMES_FOLDER, game_id)
    if not os.path.exists(game_path):
        abort(404)
    swf_file = next((f for f in os.listdir(game_path) if f.endswith('.swf')), None)
    if swf_file:
        return render_template('flash_player.html', game_id=game_id, swf_file=swf_file)
    abort(404)

@app.route('/games/<game_id>/<path:filename>')
def game_assets(game_id, filename):
    return send_from_directory(os.path.join(GAMES_FOLDER, game_id), filename)

@app.route('/thumbnail/<game_id>')
def game_thumbnail(game_id):
    thumbnail_path = os.path.join(GAMES_FOLDER, game_id, 'thumbnail.png')
    if os.path.exists(thumbnail_path):
        return send_from_directory(os.path.join(GAMES_FOLDER, game_id), 'thumbnail.png')
    abort(404)

@app.route('/api/search')
def api_search():
    """Filters games based on a search query."""
    query = request.args.get('q', '').lower()
    if not query:
        return jsonify([])

    # Flatten the games dictionary into a single list
    games_by_section = get_games_list()
    all_games = [game for games in games_by_section.values() for game in games]

    # Find games where the name contains the query string
    search_results = [
        game for game in all_games
        if query in game['name'].lower()
    ]

    return jsonify(search_results[:10]) # Return up to 10 results


@app.route('/api/games')
def api_games():
    games_by_section = get_games_list()
    return jsonify(games_by_section)

def keep_alive_pinger():
    url = "https://your-app.onrender.com/"  # Change this to your actual Render URL
    while True:
        try:
            r = requests.get(url, timeout=10)
            print(f"✅ Keep-alive ping: status {r.status_code}")
        except Exception as e:
            print(f"❌ Keep-alive ping failed: {e}")
        time.sleep(600)  # Wait 600 seconds (10 minutes)

# Start pinger in the background when the server starts
pinger_thread = threading.Thread(target=keep_alive_pinger, daemon=True)
pinger_thread.start()

if __name__ == '__main__':
    print("🚀 Starting CraveGames server...")
    app.run(debug=True)