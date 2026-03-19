import os
import sys
import uuid
import json
import ssl
import re
import urllib.error
import urllib.request
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from flask import Flask, render_template, redirect, url_for, request, flash, g, has_request_context
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, logout_user, current_user, login_required
from flask_bcrypt import Bcrypt
import logging
import time
from dotenv import load_dotenv
from sqlalchemy.dialects import registry as sqlalchemy_registry
from sqlalchemy.engine import make_url
from sqlalchemy.orm import joinedload
from werkzeug.exceptions import HTTPException
try:
    import certifi
except ImportError:
    certifi = None

load_dotenv()

app = Flask(__name__)

STATIC_IMAGE_CACHE_MAX_AGE_SECONDS = 60 * 60 * 24 * 30  # 30 days
STATIC_IMAGE_EXTENSIONS = ('.png', '.jpg', '.jpeg', '.gif', '.webp', '.svg', '.avif')


def env_value(key, default=None):
    value = os.getenv(key, default)
    if isinstance(value, str):
        prefix = f"{key}="
        if value.startswith(prefix):
            return value[len(prefix):]
    return value


HENRYGD_API_BASE_URL = env_value('HENRYGD_API_BASE_URL', 'https://ncaa-api.henrygd.me')
HENRYGD_SPORT = env_value('HENRYGD_SPORT', 'basketball-men')
HENRYGD_DIVISION = env_value('HENRYGD_DIVISION', 'd1')
TOURNAMENT_ROUND_NAMES = [
    'First Round (Round of 64)',
    'Second Round (Round of 32)',
    'Sweet 16',
    'Elite Eight',
    'Final Four',
    'Championship',
]
HENRYGD_DEPTH_TO_ROUND = {
    5: 'First Round (Round of 64)',
    4: 'Second Round (Round of 32)',
    3: 'Sweet 16',
    2: 'Elite Eight',
    1: 'Final Four',
    0: 'Championship',
}


try:
    app.config['TOURNAMENT_YEAR'] = int(env_value('TOURNAMENT_YEAR', '2026'))
except ValueError:
    app.config['TOURNAMENT_YEAR'] = 2026

secret_key = env_value('SECRET_KEY')
if not secret_key:
    secret_key_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'secret_key.txt')
    try:
        with open(secret_key_path, 'r') as f:
            secret_key = f.read().strip()
    except FileNotFoundError:
        # Avoid cold-start crashes on serverless; use an ephemeral key as last resort.
        secret_key = os.urandom(32).hex()
        print(
            "WARNING: SECRET_KEY env var and secret_key.txt are missing; using ephemeral key.",
            file=sys.stderr,
        )
app.config['SECRET_KEY'] = secret_key


def normalize_database_url(raw_url):
    if not raw_url:
        return None
    url = raw_url
    if url.startswith('postgres://'):
        url = url.replace('postgres://', 'postgresql://', 1)
    if not url.startswith('postgresql://'):
        return url

    parts = urlsplit(url)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    # Supabase requires TLS; enforce it unless explicitly set.
    query.setdefault('sslmode', 'require')
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))


def mask_database_url(url):
    if not url:
        return 'not-set'
    try:
        parts = urlsplit(url)
    except Exception:
        return 'invalid-url'
    if '@' in parts.netloc:
        _, host_part = parts.netloc.rsplit('@', 1)
    else:
        host_part = parts.netloc
    return f"{parts.scheme}://***@{host_part}{parts.path}"


def build_local_sqlite_url():
    if os.getenv('VERCEL') == '1':
        local_base = '/tmp'
    else:
        local_base = app.instance_path
    os.makedirs(local_base, exist_ok=True)
    local_db_name = f"mm{app.config['TOURNAMENT_YEAR']}.db"
    return f"sqlite:///{os.path.join(local_base, local_db_name)}"


def is_supported_sqlalchemy_url(url):
    try:
        parsed = make_url(url)
        sqlalchemy_registry.load(parsed.drivername)
        return True, None
    except Exception as exc:
        return False, exc


database_url = env_value('DATABASE_URL')
database_url = normalize_database_url(database_url)
if not database_url:
    database_url = build_local_sqlite_url()

is_supported_url, url_error = is_supported_sqlalchemy_url(database_url)
if not is_supported_url:
    print(
        f"WARNING: Invalid DATABASE_URL '{mask_database_url(database_url)}': {url_error}. "
        "Falling back to local SQLite.",
        file=sys.stderr,
    )
    database_url = build_local_sqlite_url()

app.config['SQLALCHEMY_DATABASE_URI'] = database_url
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# Set up logging
log_level_name = env_value('LOG_LEVEL', 'INFO').upper()
log_level = getattr(logging, log_level_name, logging.INFO)
logging.basicConfig(
    level=log_level,
    format='%(asctime)s %(levelname)s %(name)s [%(request_id)s] %(message)s',
)
logger = logging.getLogger(__name__)

db = SQLAlchemy(app)
bcrypt = Bcrypt(app)
login_manager = LoginManager(app)
login_manager.login_view = 'login'


class RequestIdFilter(logging.Filter):
    def filter(self, record):
        record.request_id = getattr(g, 'request_id', '-') if has_request_context() else '-'
        return True


request_id_filter = RequestIdFilter()
for handler in logging.getLogger().handlers:
    handler.addFilter(request_id_filter)


logger.info(
    "App startup: env=%s vercel=%s tournament_year=%s db=%s",
    os.getenv('FLASK_ENV', 'production'),
    os.getenv('VERCEL', '0'),
    app.config['TOURNAMENT_YEAR'],
    mask_database_url(database_url),
)


@app.before_request
def assign_request_id():
    g.request_id = request.headers.get('x-request-id') or uuid.uuid4().hex[:12]
    logger.info("Request start %s %s", request.method, request.path)


@app.after_request
def log_response(response):
    logger.info("Request end %s %s -> %s", request.method, request.path, response.status_code)
    response.headers['X-Request-ID'] = getattr(g, 'request_id', '-')
    request_path = request.path.lower()
    if request_path.startswith('/static/') and request_path.endswith(STATIC_IMAGE_EXTENSIONS):
        response.headers['Cache-Control'] = f'public, max-age={STATIC_IMAGE_CACHE_MAX_AGE_SECONDS}'
    return response


@app.errorhandler(Exception)
def handle_unexpected_exception(error):
    if isinstance(error, HTTPException):
        return error
    error_id = getattr(g, 'request_id', uuid.uuid4().hex[:12])
    logger.exception("Unhandled exception error_id=%s path=%s", error_id, request.path)
    return (
        f"Internal Server Error. Error ID: {error_id}. Check server logs for details.",
        500,
    )

@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))

# Models
class User(db.Model, UserMixin):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(120), nullable=False)
    points = db.Column(db.Integer, default=0)
    is_admin = db.Column(db.Boolean, default=False)
    fun_name = db.Column(db.String(100), default='')
    picture = db.Column(db.String(100), default='default.png')

    def set_password(self, password):
        self.password_hash = bcrypt.generate_password_hash(password).decode('utf-8')

    def check_password(self, password):
        return bcrypt.check_password_hash(self.password_hash, password)

class Round(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(50), nullable=False)
    point_value = db.Column(db.Integer, default=2)
    closed = db.Column(db.Boolean, default=False)
    closed_for_selection = db.Column(db.Boolean, default=False)
    games = db.relationship('Game', backref='round')

class Game(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    round_id = db.Column(db.Integer, db.ForeignKey('round.id'), nullable=False)
    team1 = db.Column(db.String(100), nullable=False)
    team2 = db.Column(db.String(100), nullable=False)
    winner = db.Column(db.String(100), nullable=True)
    picks = db.relationship('Pick', back_populates='game', cascade='all, delete-orphan')

class Pick(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    game_id = db.Column(db.Integer, db.ForeignKey('game.id'), nullable=False)
    picked_team = db.Column(db.String(100), nullable=False)
    wager = db.Column(db.Integer, default=0)
    points = db.Column(db.Integer, default=0)
    user = db.relationship('User', backref='picks')
    game = db.relationship('Game', back_populates='picks')

# Helper Functions
def parse_non_negative_int(value, default=0):
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return default


def normalize_team_name(name):
    if not name:
        return ''
    canonical = re.sub(r'[^a-z0-9 ]+', ' ', str(name).lower())
    canonical = re.sub(r'\s+', ' ', canonical).strip()
    if not canonical:
        return ''

    tokens = canonical.split()
    if tokens and tokens[0] == 'st':
        tokens[0] = 'saint'
    if len(tokens) >= 2 and tokens[-1] == 'st' and tokens[-2] != 'saint':
        tokens[-1] = 'state'

    canonical = ' '.join(tokens)
    alias_map = {
        'saint francis u': 'saint francis',
        'st francis u': 'saint francis',
        'texas a m': 'texas am',
        'texas a and m': 'texas am',
        'saint mary s': 'saint marys',
        'saint marys ca': 'saint marys',
        'saint peters': 'saint peters',
        'st peters': 'saint peters',
        'queens n c': 'queens',
        'miami fl': 'miami fla',
    }
    return alias_map.get(canonical, canonical)


def build_henrygd_ssl_context():
    custom_ca_bundle = os.getenv('HENRYGD_CA_BUNDLE') or os.getenv('SSL_CERT_FILE')
    if custom_ca_bundle:
        return ssl.create_default_context(cafile=custom_ca_bundle)
    if certifi is not None:
        return ssl.create_default_context(cafile=certifi.where())
    return ssl.create_default_context()


def fetch_henrygd_bracket_payload(year):
    endpoint = (
        f"{HENRYGD_API_BASE_URL.rstrip('/')}"
        f"/brackets/{HENRYGD_SPORT}/{HENRYGD_DIVISION}/{year}"
    )
    req = urllib.request.Request(
        endpoint,
        headers={
            'Accept': 'application/json',
            'User-Agent': 'march-madness-sync/1.0',
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30, context=build_henrygd_ssl_context()) as response:
            body = response.read().decode('utf-8')
            return json.loads(body) if body else {}
    except urllib.error.HTTPError as exc:
        error_body = exc.read().decode('utf-8')
        raise RuntimeError(f"HenryGD API error {exc.code}: {error_body}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"HenryGD API network error: {exc.reason}") from exc
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"HenryGD API returned invalid JSON: {exc}") from exc


def build_henrygd_games_by_round(payload):
    championships = payload.get('championships')
    if not championships or not isinstance(championships, list):
        raise RuntimeError("HenryGD response missing 'championships'")
    bracket_games = championships[0].get('games') or []
    if not isinstance(bracket_games, list):
        raise RuntimeError("HenryGD response has invalid 'games' format")

    games_by_position = {}
    for game in bracket_games:
        position_id = game.get('bracketPositionId')
        if position_id is not None:
            games_by_position[str(position_id)] = game

    depth_cache = {}

    def depth_for_position(position_id, trail=None):
        if position_id in depth_cache:
            return depth_cache[position_id]
        game = games_by_position.get(position_id)
        if not game:
            return 0
        parent_id = game.get('victorBracketPositionId')
        if parent_id is None:
            depth_cache[position_id] = 0
            return 0
        parent_key = str(parent_id)
        if parent_key not in games_by_position:
            depth_cache[position_id] = 0
            return 0
        if trail and position_id in trail:
            depth_cache[position_id] = 0
            return 0
        next_trail = set(trail or set())
        next_trail.add(position_id)
        depth_cache[position_id] = 1 + depth_for_position(parent_key, next_trail)
        return depth_cache[position_id]

    games_by_round = {}
    for game in bracket_games:
        position_id = game.get('bracketPositionId')
        if position_id is None:
            continue
        depth = depth_for_position(str(position_id))
        round_name = HENRYGD_DEPTH_TO_ROUND.get(depth)
        if not round_name:
            continue

        teams = []
        winner_name = None
        for team in game.get('teams') or []:
            team_name = team.get('nameShort') or team.get('nameFull') or team.get('seoname')
            if not team_name:
                continue
            teams.append(team_name)
            if team.get('isWinner'):
                winner_name = team_name

        if len(teams) < 2:
            continue

        games_by_round.setdefault(round_name, []).append(
            {
                'position_id': position_id,
                'team1': teams[0],
                'team2': teams[1],
                'winner': winner_name,
            }
        )

    for round_games in games_by_round.values():
        round_games.sort(key=lambda game: game['position_id'])

    return games_by_round


def resolve_round_winner_name(local_game, external_winner_name):
    winner_key = normalize_team_name(external_winner_name)
    if winner_key == normalize_team_name(local_game.team1):
        return local_game.team1
    if winner_key == normalize_team_name(local_game.team2):
        return local_game.team2
    return None


def apply_henrygd_winners_to_round(round_obj, external_games):
    if not round_obj or not external_games:
        return 0

    local_games = list(round_obj.games)
    if not local_games:
        return 0

    local_pair_map = {}
    for game in local_games:
        pair_key = frozenset({normalize_team_name(game.team1), normalize_team_name(game.team2)})
        local_pair_map.setdefault(pair_key, []).append(game)

    updated = 0
    used_game_ids = set()
    for external_game in external_games:
        pair_key = frozenset(
            {
                normalize_team_name(external_game['team1']),
                normalize_team_name(external_game['team2']),
            }
        )
        candidates = [game for game in local_pair_map.get(pair_key, []) if game.id not in used_game_ids]
        if len(candidates) != 1:
            continue

        game = candidates[0]
        used_game_ids.add(game.id)
        external_winner = external_game.get('winner')
        if not external_winner:
            continue

        resolved_winner = resolve_round_winner_name(game, external_winner)
        if resolved_winner and game.winner != resolved_winner:
            game.winner = resolved_winner
            updated += 1

    return updated


def sync_tournament_from_henrygd(payload=None):
    payload = payload or fetch_henrygd_bracket_payload(app.config['TOURNAMENT_YEAR'])
    external_games_by_round = build_henrygd_games_by_round(payload)

    summary = {
        'winners_updated': 0,
        'rounds_closed': [],
        'rounds_created': [],
    }
    rounds_by_name = {round_obj.name: round_obj for round_obj in Round.query.order_by(Round.id).all()}

    for index, round_name in enumerate(TOURNAMENT_ROUND_NAMES):
        round_obj = rounds_by_name.get(round_name)
        if not round_obj and index > 0:
            prev_round_name = TOURNAMENT_ROUND_NAMES[index - 1]
            prev_round = rounds_by_name.get(prev_round_name)
            if prev_round and prev_round.closed and len(prev_round.games) >= 2 and all(game.winner for game in prev_round.games):
                calculate_points(prev_round)
                round_obj = create_next_round(prev_round)
                rounds_by_name[round_obj.name] = round_obj
                summary['rounds_created'].append(round_obj.name)
        if not round_obj:
            continue

        winners_updated = apply_henrygd_winners_to_round(round_obj, external_games_by_round.get(round_name, []))
        summary['winners_updated'] += winners_updated
        if winners_updated:
            db.session.commit()

        calculate_points(round_obj)

        if round_obj.games and all(game.winner for game in round_obj.games):
            has_next_round = round_name != TOURNAMENT_ROUND_NAMES[-1]
            if has_next_round and len(round_obj.games) >= 2:
                next_round_name = TOURNAMENT_ROUND_NAMES[index + 1]
                if next_round_name not in rounds_by_name:
                    next_round = create_next_round(round_obj)
                    rounds_by_name[next_round.name] = next_round
                    summary['rounds_created'].append(next_round.name)

    return summary


def calculate_points(round):
    games = Game.query.filter_by(round_id=round.id).filter(Game.winner.isnot(None)).all()
    picks = Pick.query.filter(Pick.game_id.in_([g.id for g in games])).all()
    for pick in picks:
        game = pick.game
        if game.winner:
            if pick.picked_team == game.winner:
                if round.name == 'Championship':
                    pick.points = pick.wager
                else:
                    pick.points = round.point_value
            else:
                if round.name == 'Championship':
                    pick.points = -pick.wager
                else:
                    pick.points = 0
    db.session.commit()

def create_next_round(current_round):
    idx = TOURNAMENT_ROUND_NAMES.index(current_round.name)
    next_round_name = TOURNAMENT_ROUND_NAMES[idx + 1]
    point_value = current_round.point_value * 2 if next_round_name != 'Championship' else current_round.point_value
    next_round = Round(name=next_round_name, point_value=point_value, closed=True, closed_for_selection=True)
    db.session.add(next_round)
    db.session.commit()
    games = Game.query.filter_by(round_id=current_round.id).order_by(Game.id).all()
    for i in range(0, len(games), 2):
        team1 = games[i].winner
        team2 = games[i + 1].winner if i + 1 < len(games) else None
        if team1 and team2:
            db.session.add(Game(round_id=next_round.id, team1=team1, team2=team2))
    db.session.commit()
    return next_round

def get_users_with_points():
    closed_round_ids = [r.id for r in Round.query.filter_by(closed=True).all()]
    points_subquery = db.session.query(
        Pick.user_id,
        db.func.sum(Pick.points).label('total_points')
    ).join(Game).filter(Game.round_id.in_(closed_round_ids)).group_by(Pick.user_id).subquery()
    users_with_points = db.session.query(User, points_subquery.c.total_points).outerjoin(points_subquery, User.id == points_subquery.c.user_id).all()
    users = []
    for user, total_points in users_with_points:
        user.points = total_points or 0
        users.append(user)
    return sorted(users, key=lambda u: (-u.points, u.fun_name.lower()))


def build_leaderboard_pick_data(users):
    closed_rounds = Round.query.filter_by(closed=True).order_by(Round.id.desc()).all()
    leaderboard_picks = {user.id: {} for user in users}
    if not closed_rounds or not users:
        return closed_rounds, leaderboard_picks

    closed_round_ids = [round_obj.id for round_obj in closed_rounds]
    games = Game.query.filter(Game.round_id.in_(closed_round_ids)).order_by(Game.round_id.desc(), Game.id).all()
    games_by_round = {round_obj.id: [] for round_obj in closed_rounds}
    for game in games:
        games_by_round.setdefault(game.round_id, []).append(game)

    game_ids = [game.id for game in games]
    user_ids = [user.id for user in users]
    picks = []
    if game_ids and user_ids:
        picks = Pick.query.filter(Pick.game_id.in_(game_ids), Pick.user_id.in_(user_ids)).all()
    picks_by_user_game = {(pick.user_id, pick.game_id): pick for pick in picks}

    for user in users:
        for round_obj in closed_rounds:
            rows = []
            round_total = 0
            for game in games_by_round.get(round_obj.id, []):
                pick = picks_by_user_game.get((user.id, game.id))
                picked_team = pick.picked_team if pick else None
                winner = game.winner
                points = pick.points if pick else 0

                if winner and picked_team == winner:
                    result_label = 'Won'
                    result_key = 'win'
                elif winner and picked_team:
                    result_label = 'Lost'
                    result_key = 'loss'
                elif winner and not picked_team:
                    result_label = 'No Pick'
                    result_key = 'loss'
                else:
                    result_label = 'Pending'
                    result_key = 'pending'

                rows.append({
                    'game': f'{game.team1} vs {game.team2}',
                    'team1': game.team1,
                    'team2': game.team2,
                    'picked_team': picked_team or 'No pick',
                    'winner': winner or 'Pending',
                    'result_label': result_label,
                    'result_key': result_key,
                    'points': points,
                })
                round_total += points

            leaderboard_picks[user.id][round_obj.id] = {
                'rows': rows,
                'round_total': round_total,
            }

    return closed_rounds, leaderboard_picks

# Context Processor for Navbar Points
@app.context_processor
def inject_user_points():
    tournament_year = app.config['TOURNAMENT_YEAR']
    if current_user.is_authenticated:
        closed_round_ids = [r.id for r in Round.query.filter_by(closed=True).all()]
        total_points = db.session.query(db.func.sum(Pick.points)).join(Game).filter(Pick.user_id == current_user.id, Game.round_id.in_(closed_round_ids)).scalar() or 0
        return {'user_points': total_points, 'tournament_year': tournament_year}
    return {'user_points': 0, 'tournament_year': tournament_year}

# Routes
@app.route('/')
def home():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))
    current_round = Round.query.filter_by(closed=False).order_by(Round.id).first()
    return render_template('home.html', current_round=current_round)

@app.route('/dashboard')
@login_required
def dashboard():
    users = get_users_with_points()
    winners = []
    losers = []
    if users and users[0].points != users[-1].points:
        top_pts = users[0].points
        bot_pts = users[-1].points
        winners = [u for u in users if u.points == top_pts]
        losers = [u for u in users if u.points == bot_pts]
    return render_template('dashboard.html', winners=winners, losers=losers)

@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        user = User.query.filter_by(username=username).first()
        if user and user.check_password(password):
            login_user(user)
            return redirect(url_for('dashboard'))
        else:
            flash('Invalid username or password', 'danger')
    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('home'))

@app.route('/pick', methods=['GET', 'POST'])
@login_required
def pick():
    current_round = Round.query.filter_by(closed=False, closed_for_selection=False).order_by(Round.id).first()
    
    if not current_round:
        flash('No open rounds available for picks', 'warning')
        return redirect(url_for('home'))

    games = Game.query.filter_by(round_id=current_round.id).all()
    picks = Pick.query.filter(Pick.user_id == current_user.id, Pick.game_id.in_([g.id for g in games])).all()
    existing_picks = {pick.game_id: pick for pick in picks}
    
    closed_round_ids = [r.id for r in Round.query.filter_by(closed=True).all()]
    user_total_points = db.session.query(db.func.sum(Pick.points)).join(Game).filter(Pick.user_id == current_user.id, Game.round_id.in_(closed_round_ids)).scalar() or 0

    error_game_id = None
    wager = 0
    if request.method == 'POST':
        try:
            for game in games:
                picked_team = request.form.get(f'game{game.id}')
                if not picked_team or picked_team not in [game.team1, game.team2]:
                    error_game_id = game.id
                    wager = parse_non_negative_int(request.form.get('wager', 0), default=0) if current_round.name == 'Championship' else 0
                    break
                else:
                    existing_pick = existing_picks.get(game.id)
                    if existing_pick:
                        existing_pick.picked_team = picked_team
                        if current_round.name == 'Championship':
                            wager = parse_non_negative_int(request.form.get('wager', existing_pick.wager), default=existing_pick.wager)
                            existing_pick.wager = max(0, min(wager, user_total_points))
                    else:
                        pick = Pick(user_id=current_user.id, game_id=game.id, picked_team=picked_team)
                        if current_round.name == 'Championship':
                            wager = parse_non_negative_int(request.form.get('wager', 0), default=0)
                            pick.wager = max(0, min(wager, user_total_points))
                        db.session.add(pick)

            if error_game_id:
                db.session.rollback()
                return render_template('pick.html', games=games, existing_picks=existing_picks, current_round=current_round,
                                       user_points=user_total_points, error_game_id=error_game_id, wager=wager)

            db.session.commit()
            flash('Picks submitted successfully! Points will update as games are played.', 'success')
            return redirect(url_for('home'))
        except Exception as e:
            db.session.rollback()
            flash(f'Error saving picks: {str(e)}', 'danger')
            return redirect(url_for('pick'))

    return render_template('pick.html', games=games, existing_picks=existing_picks, current_round=current_round, user_points=user_total_points, error_game_id=None, wager=0)

@app.route('/view_picks')
@login_required
def view_picks():
    start_time = time.time()
    closed_rounds = Round.query.filter_by(closed=True).order_by(Round.id.desc()).all()
    logger.debug(f"Fetched closed rounds in {time.time() - start_time:.3f} seconds")
    if not closed_rounds:
        flash('No closed rounds available to view picks', 'warning')
        return redirect(url_for('home'))

    start_time = time.time()
    users = User.query.all()
    logger.debug(f"Fetched users in {time.time() - start_time:.3f} seconds")

    start_time = time.time()
    closed_round_ids = [round.id for round in closed_rounds]
    games = Game.query.filter(Game.round_id.in_(closed_round_ids)).all()
    game_ids = [game.id for game in games]
    logger.debug(f"Fetched games in {time.time() - start_time:.3f} seconds")

    start_time = time.time()
    picks = Pick.query.options(joinedload(Pick.user), joinedload(Pick.game)).filter(Pick.game_id.in_(game_ids)).all()
    logger.debug(f"Fetched picks with joinedload in {time.time() - start_time:.3f} seconds")

    start_time = time.time()
    picks_by_game = {}
    for pick in picks:
        if pick.game_id not in picks_by_game:
            picks_by_game[pick.game_id] = []
        picks_by_game[pick.game_id].append(pick)

    games_by_round = {round.id: [g for g in games if g.round_id == round.id] for round in closed_rounds}
    for round_id, round_games in games_by_round.items():
        logger.debug(f"Round {round_id} has {len(round_games)} games")

    points_by_user_game = {}
    user_totals_by_round = {}
    for round in closed_rounds:
        points_by_user_game[round.id] = {}
        user_totals_by_round[round.id] = {}
        for user in users:
            points_by_user_game[round.id][user.id] = {}
            total = 0
            for game in games_by_round[round.id]:
                pick = next((p for p in picks_by_game.get(game.id, []) if p.user_id == user.id), None)
                points = pick.points if pick else 0
                points_by_user_game[round.id][user.id][game.id] = points
                total += points
            user_totals_by_round[round.id][user.id] = total
            user.points = total

    for round_id in user_totals_by_round:
        user_totals = [(user, user_totals_by_round[round_id][user.id]) for user in users]
        user_totals_by_round[round_id] = sorted(user_totals, key=lambda x: x[1], reverse=True)

    logger.debug(f"Processed data for view_picks in {time.time() - start_time:.3f} seconds")
    return render_template('view_picks.html', closed_rounds=closed_rounds, users=users,
                          games_by_round=games_by_round, points_by_user_game=points_by_user_game,
                          user_totals_by_round=user_totals_by_round)

@app.route('/admin', methods=['GET', 'POST'])
@login_required
def admin():
    if not current_user.is_admin:
        flash('Access denied', 'danger')
        return redirect(url_for('home'))
    all_rounds = Round.query.order_by(Round.id).all()
    selected_round_id = request.args.get('round_id', type=int) or (all_rounds[-1].id if all_rounds else None)
    selected_round = db.session.get(Round, selected_round_id) if selected_round_id else None

    # Check if the user is Chris for the prank
    is_chris = current_user.username.lower() == 'chris'

    if request.method == 'POST':
        round_id = request.form.get('round_id', type=int)
        if round_id:
            selected_round = db.session.get(Round, round_id)
            if selected_round:
                for game in selected_round.games:
                    winner = request.form.get(f'game{game.id}_winner')
                    if selected_round.name == 'First Round (Round of 64)':
                        if winner in [game.team1, game.team2]:
                            game.winner = winner
                        else:
                            game.winner = None
                    else:
                        team1 = request.form.get(f'game{game.id}_team1_select')
                        team2 = request.form.get(f'game{game.id}_team2_select')
                        prev_round_idx = TOURNAMENT_ROUND_NAMES.index(selected_round.name) - 1
                        prev_round = Round.query.filter_by(name=TOURNAMENT_ROUND_NAMES[prev_round_idx]).first()
                        prev_winners = [game.winner for game in prev_round.games if game.winner] if prev_round else []
                        if team1 in prev_winners and team2 in prev_winners and team1 != team2:
                            game.team1 = team1
                            game.team2 = team2
                            if winner in [team1, team2]:
                                game.winner = winner
                            else:
                                game.winner = None
                        else:
                            flash(f'Invalid teams selected for {game.team1} vs {game.team2}', 'danger')
                
                selected_round.closed = request.form.get('closed') == 'on'
                selected_round.closed_for_selection = selected_round.closed
                try:
                    points = int(request.form.get('point_value', 2))
                    if points > 0:
                        selected_round.point_value = points
                except ValueError:
                    flash('Invalid point value; keeping previous value', 'warning')
                
                if 'next_round' in request.form and selected_round.name != 'Championship':
                    if all(game.winner for game in selected_round.games):
                        next_round_name = TOURNAMENT_ROUND_NAMES[TOURNAMENT_ROUND_NAMES.index(selected_round.name) + 1]
                        if not Round.query.filter_by(name=next_round_name).first():
                            selected_round.closed = True
                            selected_round.closed_for_selection = True
                            db.session.commit()
                            calculate_points(selected_round)
                            next_round = create_next_round(selected_round)
                            flash(f'Next round ({next_round_name}) created', 'success')
                            return redirect(url_for('admin', round_id=next_round.id))
                        else:
                            flash(f'{next_round_name} already exists', 'warning')
                    else:
                        flash('Cannot create next round: All winners must be set', 'danger')
                
                db.session.commit()
                calculate_points(selected_round)
                flash(f'{selected_round.name} saved successfully', 'success')
        return redirect(url_for('admin', round_id=selected_round.id if selected_round else selected_round_id))
    
    prev_round = None
    if selected_round and selected_round.name != 'First Round (Round of 64)':
        prev_round_idx = TOURNAMENT_ROUND_NAMES.index(selected_round.name) - 1
        prev_round = Round.query.filter_by(name=TOURNAMENT_ROUND_NAMES[prev_round_idx]).first()
    prev_winners = [game.winner for game in prev_round.games if game.winner] if prev_round else []
    
    users = User.query.all()
    users_with_picks = []
    if selected_round:
        games = Game.query.filter_by(round_id=selected_round.id).all()
        game_ids = [game.id for game in games]
        for user in users:
            user_picks = Pick.query.filter_by(user_id=user.id).filter(Pick.game_id.in_(game_ids)).count()
            users_with_picks.append({
                'username': user.username,
                'has_picks': user_picks == len(games)
            })

    return render_template('admin.html', all_rounds=all_rounds, selected_round=selected_round, prev_winners=prev_winners, users_with_picks=users_with_picks, is_chris=is_chris)

@app.route('/admin_sync_henrygd', methods=['POST'])
@login_required
def admin_sync_henrygd():
    if not current_user.is_admin:
        flash('Access denied', 'danger')
        return redirect(url_for('home'))

    selected_round_id = request.form.get('round_id', type=int)
    try:
        summary = sync_tournament_from_henrygd()
        success_message = (
            "Data sync complete: "
            f"{summary['winners_updated']} winner(s) updated, "
            f"{len(summary['rounds_created'])} round(s) created. "
            "Round saved."
        )
        flash(success_message, 'success')
    except Exception as exc:
        logger.exception("HenryGD sync failed")
        flash(f'HenryGD sync failed: {exc}', 'danger')

    if selected_round_id:
        return redirect(url_for('admin', round_id=selected_round_id))
    return redirect(url_for('admin'))

@app.route('/admin_submit_picks', methods=['GET', 'POST'])
@login_required
def admin_submit_picks():
    if not current_user.is_admin:
        flash('Access denied', 'danger')
        return redirect(url_for('home'))
    
    all_open_rounds = Round.query.filter_by(closed=False).order_by(Round.id).all()
    selected_round_id = request.form.get('round_id', type=int) or request.args.get('round_id', type=int) or (all_open_rounds[0].id if all_open_rounds else None)
    open_round_ids = {round_obj.id for round_obj in all_open_rounds}
    if selected_round_id not in open_round_ids:
        selected_round_id = all_open_rounds[0].id if all_open_rounds else None
    current_round = db.session.get(Round, selected_round_id) if selected_round_id else None
    users = User.query.all()
    
    if not current_round:
        flash('No open rounds available for picks', 'warning')
        return redirect(url_for('admin'))
    
    games = Game.query.filter_by(round_id=current_round.id).all()
    selected_user_id = request.form.get('user_id', type=int) if request.method == 'POST' else request.args.get('user_id', type=int)
    selected_user = db.session.get(User, selected_user_id) if selected_user_id else None
    
    selected_user_points = 0
    if selected_user:
        closed_round_ids = [r.id for r in Round.query.filter_by(closed=True).all()]
        selected_user_points = db.session.query(db.func.sum(Pick.points)).join(Game).filter(Pick.user_id == selected_user.id, Game.round_id.in_(closed_round_ids)).scalar() or 0

    existing_picks = {}
    if selected_user:
        picks = Pick.query.filter(Pick.user_id == selected_user.id, Pick.game_id.in_([g.id for g in games])).all()
        existing_picks = {pick.game_id: pick for pick in picks}
    
    error_game_id = None
    wager = 0
    if request.method == 'POST' and 'submit_picks' in request.form and selected_user:
        try:
            for game in games:
                picked_team = request.form.get(f'game{game.id}')
                if not picked_team or picked_team not in [game.team1, game.team2]:
                    error_game_id = game.id
                    wager = parse_non_negative_int(request.form.get('wager', 0), default=0) if current_round.name == 'Championship' else 0
                    break
                else:
                    existing_pick = Pick.query.filter_by(user_id=selected_user.id, game_id=game.id).first()
                    if existing_pick:
                        existing_pick.picked_team = picked_team
                        if current_round.name == 'Championship':
                            wager = parse_non_negative_int(request.form.get('wager', existing_pick.wager), default=existing_pick.wager)
                            existing_pick.wager = max(0, min(wager, selected_user_points))
                    else:
                        pick = Pick(user_id=selected_user.id, game_id=game.id, picked_team=picked_team)
                        if current_round.name == 'Championship':
                            wager = parse_non_negative_int(request.form.get('wager', 0), default=0)
                            pick.wager = max(0, min(wager, selected_user_points))
                        db.session.add(pick)
            
            if error_game_id:
                db.session.rollback()
                return render_template('admin_submit_picks.html', all_open_rounds=all_open_rounds, current_round=current_round,
                                       games=games, users=users, existing_picks=existing_picks,
                                       selected_user_id=selected_user_id, selected_user=selected_user,
                                       selected_user_points=selected_user_points, error_game_id=error_game_id, wager=wager)
            
            db.session.commit()
            flash(f'Picks submitted successfully for {selected_user.username}!', 'success')
            return redirect(url_for('admin'))
        except Exception as e:
            db.session.rollback()
            flash(f'Error saving picks: {str(e)}', 'danger')
            return redirect(url_for('admin_submit_picks', round_id=current_round.id, user_id=selected_user_id))
    
    return render_template('admin_submit_picks.html', all_open_rounds=all_open_rounds, current_round=current_round, games=games, users=users, existing_picks=existing_picks, selected_user_id=selected_user_id, selected_user=selected_user, selected_user_points=selected_user_points)

@app.route('/leaderboard')
def leaderboard():
    users = get_users_with_points()
    ranks = []
    for i, user in enumerate(users):
        if i == 0 or user.points != users[i - 1].points:
            ranks.append(i + 1)
        else:
            ranks.append(ranks[-1])
    closed_rounds, leaderboard_picks = build_leaderboard_pick_data(users)
    return render_template(
        'leaderboard.html',
        users=users,
        ranks=ranks,
        closed_rounds=closed_rounds,
        leaderboard_picks=leaderboard_picks,
    )

if __name__ == '__main__':
    app.run(debug=True)
