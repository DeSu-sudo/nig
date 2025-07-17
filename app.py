# app.py
from flask import Flask, render_template, send_from_directory, abort, jsonify, request, redirect, url_for
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, logout_user, current_user, login_required
from flask_wtf import FlaskForm
from wtforms import StringField, PasswordField, SubmitField
from wtforms.validators import DataRequired, Length, EqualTo, ValidationError, Regexp
from werkzeug.security import generate_password_hash, check_password_hash
from flask_wtf.csrf import CSRFProtect
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
import os
import json
from datetime import datetime, timedelta
import random

app = Flask(__name__)

# --- Configuration ---
app.config['SECRET_KEY'] = 'a-very-secret-key-that-you-should-change'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///database.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

GAMES_FOLDER = 'games'
PLAY_COUNTS_FILE = 'play_counts.json'

# --- Extensions Setup ---
db = SQLAlchemy(app)
csrf = CSRFProtect(app)
limiter = Limiter(
    get_remote_address,
    app=app
)
login_manager = LoginManager(app)
login_manager.login_view = 'login'
login_manager.init_app(app)

# --- Database Models ---
class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(150), unique=True, nullable=False)
    password_hash = db.Column(db.String(150), nullable=False)
    favorites = db.relationship('Favorite', backref='user', lazy=True)
    comments = db.relationship('Comment', backref='user', lazy=True)
    ratings = db.relationship('Rating', backref='user', lazy=True)
    crave_coins = db.Column(db.Integer, default=0)
    inventory = db.relationship('UserInventory', backref='user', lazy=True)
    active_avatar_item_id = db.Column(db.Integer, db.ForeignKey('item.id'), nullable=True)
    active_avatar = db.relationship('Item')

class Favorite(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    game_id = db.Column(db.String(100), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)

class Comment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    text = db.Column(db.Text, nullable=False)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)
    game_id = db.Column(db.String(100), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)

class Rating(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    value = db.Column(db.Integer, nullable=False)
    game_id = db.Column(db.String(100), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    __table_args__ = (db.UniqueConstraint('user_id', 'game_id', name='_user_game_uc'),)

class Item(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    description = db.Column(db.Text)
    price = db.Column(db.Integer, nullable=False)
    image_url = db.Column(db.String(200), nullable=False)
    item_type = db.Column(db.String(50), nullable=False, default='avatar')

class UserInventory(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    item_id = db.Column(db.Integer, db.ForeignKey('item.id'), nullable=False)
    item = db.relationship('Item')


@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

# --- Form Classes ---
class RegistrationForm(FlaskForm):
    username = StringField('Username', validators=[
        DataRequired(), 
        Length(min=4, max=20),
        Regexp('^[a-z0-9_-]+$', message='Username must contain only lowercase letters, numbers, underscores, or hyphens.')
    ])
    password = PasswordField('Password', validators=[DataRequired(), Length(min=6)])
    confirm_password = PasswordField('Confirm Password', validators=[DataRequired(), EqualTo('password', message='Passwords must match.')])
    submit = SubmitField('Sign Up')

    def validate_username(self, username):
        if User.query.filter_by(username=username.data).first():
            raise ValidationError('That username is taken. Please choose a different one.')

class LoginForm(FlaskForm):
    username = StringField('Username', validators=[DataRequired()])
    password = PasswordField('Password', validators=[DataRequired()])
    submit = SubmitField('Login')

# --- Helper Functions ---
def load_games_dataset():
    try:
        with open('games_dataset.json', 'r') as f: return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError): return {}

def load_play_counts():
    try:
        with open(PLAY_COUNTS_FILE, 'r') as f: return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError): return {}

def save_play_counts(play_counts):
    with open(PLAY_COUNTS_FILE, 'w') as f: json.dump(play_counts, f, indent=4)

def get_or_create_game_stats(game_id):
    play_counts = load_play_counts()
    game_data = play_counts.get(game_id, {})
    game_data.setdefault('total_plays', 0)
    game_data.setdefault('weekly_plays', [])
    game_data.setdefault('ratings', [])
    game_data.setdefault('average_rating', 0)
    game_data.setdefault('rating_count', 0)
    play_counts[game_id] = game_data
    return play_counts

def increment_play_count(game_id):
    play_counts = get_or_create_game_stats(game_id)
    game_data = play_counts[game_id]
    game_data['total_plays'] += 1
    today = datetime.now().date().isoformat()
    cutoff = (datetime.now() - timedelta(days=7)).date().isoformat()
    game_data['weekly_plays'] = [d for d in game_data.get('weekly_plays', []) if d['date'] >= cutoff]
    today_entry = next((d for d in game_data['weekly_plays'] if d['date'] == today), None)
    if today_entry: today_entry['plays'] += 1
    else: game_data['weekly_plays'].append({'date': today, 'plays': 1})
    save_play_counts(play_counts)

def get_all_categories():
    games_dataset = load_games_dataset()
    categories = set(cat for info in games_dataset.values() for cat in info.get('category', []))
    return sorted(list(categories))

@app.context_processor
def inject_categories():
    return dict(all_categories=get_all_categories())

def get_games_list(sort_by='play_count', user=None):
    games_dataset = load_games_dataset()
    play_counts = load_play_counts()
    all_games = []
    if not os.path.exists(GAMES_FOLDER): return {}
    for game_folder in os.listdir(GAMES_FOLDER):
        if not os.path.isdir(os.path.join(GAMES_FOLDER, game_folder)): continue
        game_info = games_dataset.get(game_folder, {})
        stats = play_counts.get(game_folder, {})
        game_data = {
            'id': game_folder,
            'name': game_info.get('name', game_folder.replace('-', ' ').title()),
            'category': game_info.get('category', []),
            'date_added': game_info.get('date_added', '2023-01-01'),
            'play_count': stats.get('total_plays', 0),
            'average_rating': stats.get('average_rating', 0),
            'rating_count': stats.get('rating_count', 0),
            'is_favorite': False
        }
        all_games.append(game_data)
    
    sort_key = 'average_rating' if sort_by == 'rating' else 'date_added' if sort_by == 'newest' else 'play_count'
    all_games.sort(key=lambda x: x[sort_key], reverse=True)

    if user and user.is_authenticated:
        favorite_game_ids = {fav.game_id for fav in user.favorites}
        for game in all_games:
            if game['id'] in favorite_game_ids: game['is_favorite'] = True
    
    games_by_section = {}
    for game in all_games:
        section = games_dataset.get(game['id'], {}).get('section', game['category'][0] if game['category'] else 'Games')
        games_by_section.setdefault(section, []).append(game)
    return games_by_section

def get_game_details(game_id, user=None):
    games_dataset = load_games_dataset()
    game_info = games_dataset.get(game_id, {})
    game_path = os.path.join(GAMES_FOLDER, game_id)
    if not os.path.isdir(game_path): return None
    game_type = detect_game_type(game_path) if not game_info.get('iframe_url') else 'iframe'
    if not game_type: return None
    
    game_data = {
        'id': game_id, 'name': game_info.get('name', game_id.replace('-', ' ').title()),
        'type': game_type, 'category': game_info.get('category', []),
        'description': game_info.get('description', ''), 'instructions': game_info.get('instructions', ''),
        'iframe_url': game_info.get('iframe_url'), 'average_rating': 0,
        'rating_count': 0, 'is_favorite': False
    }

    if game_type == 'html': game_data['entry_point'] = get_html_entry_point(game_path)

    if user and user.is_authenticated:
        play_counts = load_play_counts()
        stats = play_counts.get(game_id, {})
        game_data['average_rating'] = stats.get('average_rating', 0)
        game_data['rating_count'] = stats.get('rating_count', 0)
        game_data['is_favorite'] = Favorite.query.filter_by(user_id=user.id, game_id=game_id).first() is not None
        
    return game_data

def detect_game_type(game_path):
    files = os.listdir(game_path)
    if any(f.lower().endswith('.html') for f in files): return 'html'
    if any(f.lower().endswith('.swf') for f in files): return 'flash'
    return None

def get_html_entry_point(game_path):
    for file in os.listdir(game_path):
        if file.lower() in ['index.html', 'index.htm']: return file
    return None

def render_or_jsonify(content_template, **context):
    if request.args.get('partial') == 'true':
        html = render_template(content_template, **context)
        title = context.get('title', 'CraveGames')
        return jsonify(html=html, title=title)
    context['content_template'] = content_template
    return render_template('base.html', **context)

# --- Page-serving Routes ---
@app.route('/')
def index():
    return render_or_jsonify('index_content.html', games_by_section=get_games_list(user=current_user), active_page='Home', title='Home')

@app.route('/game/<game_id>')
def game_page(game_id):
    increment_play_count(game_id)
    current_game = get_game_details(game_id, user=current_user)
    if not current_game: abort(404)
    all_games_flat = [game for section in get_games_list(user=current_user).values() for game in section]
    other_games = [g for g in all_games_flat if g['id'] != game_id][:12]
    return render_or_jsonify('game_content.html', game=current_game, other_games=other_games, active_page=current_game.get('category', [None])[0], title=current_game['name'])

@app.route('/category/<category_name>')
def category_page(category_name):
    sort_by = request.args.get('sort', 'play_count')
    all_games_flat = [game for section in get_games_list(sort_by=sort_by, user=current_user).values() for game in section]
    games_in_category = [g for g in all_games_flat if category_name in g.get('category', [])]
    return render_or_jsonify('category_content.html', category_name=category_name, games=games_in_category, active_page=category_name, title=f"{category_name} Games", current_sort=sort_by)

# --- User, Favorites, and Content Routes ---
@app.route('/register', methods=['GET', 'POST'])
def register():
    if current_user.is_authenticated: return redirect(url_for('index'))
    form = RegistrationForm()
    if form.validate_on_submit():
        user = User(username=form.username.data, password_hash=generate_password_hash(form.password.data))
        db.session.add(user)
        db.session.commit()
        login_user(user)
        return jsonify({'status': 'success', 'redirect_url': url_for('index')})
    if request.method == 'POST': return jsonify({'status': 'error', 'errors': form.errors}), 400
    return render_or_jsonify('register.html', title="Register", form=form, active_page='Register')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated: return redirect(url_for('index'))
    form = LoginForm()
    if form.validate_on_submit():
        user = User.query.filter_by(username=form.username.data).first()
        if user and check_password_hash(user.password_hash, form.password.data):
            login_user(user, remember=True)
            return jsonify({'status': 'success', 'redirect_url': url_for('index')})
        else:
            return jsonify({'status': 'error', 'errors': {'form': ['Login failed. Please check username and password.']}}), 401
    if request.method == 'POST': return jsonify({'status': 'error', 'errors': form.errors}), 400
    return render_or_jsonify('login.html', title="Login", form=form, active_page='Login')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('index'))

@app.route('/favorites')
@login_required
def favorites():
    all_games_flat = {g['id']: g for section in get_games_list(user=current_user).values() for g in section}
    favorite_ids = {fav.game_id for fav in current_user.favorites}
    user_favorites = [all_games_flat[game_id] for game_id in favorite_ids if game_id in all_games_flat]
    return render_or_jsonify('favorites.html', games=user_favorites, title="My Favorites", active_page='Favorites')

@app.route('/random')
def random_game():
    all_games_flat = [game for section in get_games_list().values() for game in section]
    if not all_games_flat: return jsonify({'error': 'No games available'}), 404
    return jsonify({'url': url_for('game_page', game_id=random.choice(all_games_flat)['id'])})

@app.route('/make-more')
@login_required
def make_more():
    return render_or_jsonify('make_more_content.html', title="Earn Crave Coins", active_page='Make More')

@app.route('/store')
@login_required
def store():
    items = Item.query.all()
    user_inventory_ids = {inv.item_id for inv in current_user.inventory}
    return render_or_jsonify('store_content.html', title="Store", items=items, user_inventory_ids=user_inventory_ids, active_page='Store')

@app.route('/inventory')
@login_required
def inventory():
    inventory_items = [inv.item for inv in current_user.inventory]
    return render_or_jsonify('inventory_content.html', title="My Collection", active_page='Inventory', items=inventory_items)


# --- API Endpoints ---
@app.route('/api/rate/<game_id>', methods=['POST'])
@login_required
def rate_game(game_id):
    rating_value = request.json.get('rating')
    if rating_value not in range(1, 6): return jsonify({'error': 'Invalid rating'}), 400
    existing_rating = Rating.query.filter_by(user_id=current_user.id, game_id=game_id).first()
    if existing_rating: existing_rating.value = rating_value
    else: db.session.add(Rating(user_id=current_user.id, game_id=game_id, value=rating_value))
    db.session.commit()
    all_ratings = Rating.query.filter_by(game_id=game_id).all()
    rating_count = len(all_ratings)
    average_rating = sum(r.value for r in all_ratings) / rating_count if rating_count > 0 else 0
    play_counts = get_or_create_game_stats(game_id)
    game_data = play_counts[game_id]
    game_data['average_rating'] = round(average_rating, 2)
    game_data['rating_count'] = rating_count
    save_play_counts(play_counts)
    return jsonify({'average_rating': game_data['average_rating'], 'rating_count': game_data['rating_count']})

@app.route('/api/favorite/<game_id>', methods=['POST'])
@login_required
def toggle_favorite(game_id):
    favorite = Favorite.query.filter_by(user_id=current_user.id, game_id=game_id).first()
    if favorite:
        db.session.delete(favorite)
        is_favorited = False
    else:
        db.session.add(Favorite(user_id=current_user.id, game_id=game_id))
        is_favorited = True
    db.session.commit()
    return jsonify({'is_favorited': is_favorited})

@app.route('/api/comments/<game_id>', methods=['GET', 'POST'])
def handle_comments(game_id):
    if request.method == 'POST':
        if not current_user.is_authenticated:
            return jsonify({'error': 'Authentication required'}), 401
        
        text = request.json.get('text', '').strip()

        # Validation Checks
        if not text:
            return jsonify({'error': 'Comment text cannot be empty.'}), 400
        if len(text) > 500:
            return jsonify({'error': 'Comment cannot exceed 500 characters.'}), 400

        comment = Comment(text=text, game_id=game_id, user_id=current_user.id)
        db.session.add(comment)
        db.session.commit()
        
        avatar_url = url_for('static', filename=comment.user.active_avatar.image_url) if comment.user.active_avatar else url_for('static', filename='images/avatar_parts/default.png')
        return jsonify({
            'id': comment.id,
            'text': comment.text,
            'timestamp': comment.timestamp.isoformat(),
            'username': comment.user.username,
            'avatar_url': avatar_url
        }), 201

    page = request.args.get('page', 1, type=int)
    pagination = Comment.query.filter_by(game_id=game_id).order_by(Comment.timestamp.desc()).paginate(page=page, per_page=10, error_out=False)
    
    comments_data = []
    for c in pagination.items:
        avatar_url = url_for('static', filename=c.user.active_avatar.image_url) if c.user.active_avatar else url_for('static', filename='images/avatar_parts/default.png')
        comments_data.append({
            'id': c.id, 
            'text': c.text, 
            'timestamp': c.timestamp.isoformat(), 
            'username': c.user.username,
            'avatar_url': avatar_url
        })

    return jsonify({
        'comments': comments_data,
        'has_next': pagination.has_next
    })


@app.route('/api/search')
def api_search():
    query = request.args.get('q', '').lower()
    if not query: return jsonify([])
    all_games = [game for games in get_games_list().values() for game in games]
    search_results = [game for game in all_games if query in game['name'].lower()][:10]
    return jsonify(search_results)

@app.route('/api/claim-coin', methods=['POST'])
@login_required
@limiter.limit("10 per minute")
def claim_coin():
    current_user.crave_coins += 1
    db.session.commit()
    return jsonify({'success': True, 'new_balance': current_user.crave_coins})

@app.route('/api/buy-item/<int:item_id>', methods=['POST'])
@login_required
def buy_item(item_id):
    item = Item.query.get_or_404(item_id)
    if UserInventory.query.filter_by(user_id=current_user.id, item_id=item.id).first():
        return jsonify({'success': False, 'error': 'You already own this item.'}), 400
    if current_user.crave_coins < item.price:
        return jsonify({'success': False, 'error': 'Not enough Crave Coins.'}), 400
    
    current_user.crave_coins -= item.price
    inventory_item = UserInventory(user_id=current_user.id, item_id=item.id)
    db.session.add(inventory_item)
    db.session.commit()
    
    return jsonify({'success': True, 'new_balance': current_user.crave_coins})

@app.route('/api/set-active-avatar/<int:item_id>', methods=['POST'])
@login_required
def set_active_avatar(item_id):
    # Check if user owns the item
    if not UserInventory.query.filter_by(user_id=current_user.id, item_id=item_id).first():
        return jsonify({'success': False, 'error': 'You do not own this item.'}), 403
    
    current_user.active_avatar_item_id = item_id
    db.session.commit()
    
    return jsonify({'success': True, 'new_avatar_url': url_for('static', filename=current_user.active_avatar.image_url)})


# --- Asset and Utility Routes ---
@app.route('/play/<game_id>')
def play_game(game_id):
    game_path = os.path.join(GAMES_FOLDER, game_id)
    if not os.path.exists(game_path): abort(404)
    swf_file = next((f for f in os.listdir(game_path) if f.endswith('.swf')), None)
    if swf_file: return render_template('flash_player.html', game_id=game_id, swf_file=swf_file)
    abort(404)

@app.route('/games/<game_id>/<path:filename>')
def game_assets(game_id, filename):
    return send_from_directory(os.path.join(GAMES_FOLDER, game_id), filename)

@app.route('/thumbnail/<game_id>')
def game_thumbnail(game_id):
    return send_from_directory(os.path.join(GAMES_FOLDER, game_id), 'thumbnail.png')

def seed_database():
    if Item.query.first() is None:
        items_to_add = [
            Item(name='Purple Crave-atar', description='A purple CraveGames original.', price=50, image_url='images/avatar_parts/avatar1.png'),
            Item(name='Green Crave-atar', description='A green CraveGames original.', price=50, image_url='images/avatar_parts/avatar2.png'),
            Item(name='Orange Crave-atar', description='An orange CraveGames original.', price=50, image_url='images/avatar_parts/avatar3.png'),
            Item(name='Gamer Crave-atar', description='For the true gamer.', price=250, image_url='images/avatar_parts/avatar4.png'),
            Item(name='Gold Crave-atar', description='A rare, golden avatar.', price=1000, image_url='images/avatar_parts/avatar5.png')
        ]
        db.session.bulk_save_objects(items_to_add)
        db.session.commit()
        print("Database seeded with initial items.")


if __name__ == '__main__':
    with app.app_context():
        db.create_all()
        seed_database()
    app.run(debug=True)