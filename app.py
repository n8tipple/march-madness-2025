"""
March Madness 2026 - Bracket Prediction App
A comprehensive tournament bracket prediction and tracking system.
"""

import os
import logging
from datetime import datetime
from functools import wraps

from flask import Flask, render_template, redirect, url_for, request, flash, jsonify
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, logout_user, current_user, login_required
from flask_bcrypt import Bcrypt
from sqlalchemy.orm import joinedload
from sqlalchemy import func

# =============================================================================
# APP CONFIGURATION
# =============================================================================

app = Flask(__name__)

# Secret Key - use environment variable or file
secret_key = os.getenv('SECRET_KEY')
if not secret_key:
    try:
        with open('secret_key.txt', 'r') as f:
            secret_key = f.read().strip()
    except FileNotFoundError:
        secret_key = os.urandom(32).hex()
        with open('secret_key.txt', 'w') as f:
            f.write(secret_key)
app.config['SECRET_KEY'] = secret_key

# Database Configuration - prioritize environment variable
database_url = os.getenv('DATABASE_URL')
if database_url:
    # Handle Heroku-style postgres:// URLs
    if database_url.startswith('postgres://'):
        database_url = database_url.replace('postgres://', 'postgresql://', 1)
    app.config['SQLALCHEMY_DATABASE_URI'] = database_url
else:
    # Default to SQLite for local development
    app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///mm2026.db'

app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {
    'pool_recycle': 300,
    'pool_pre_ping': True
}

# Logging Configuration
log_level = logging.DEBUG if os.getenv('FLASK_ENV') == 'development' else logging.INFO
logging.basicConfig(
    level=log_level,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Initialize Extensions
db = SQLAlchemy(app)
bcrypt = Bcrypt(app)
login_manager = LoginManager(app)
login_manager.login_view = 'login'
login_manager.login_message_category = 'info'

# =============================================================================
# CONSTANTS
# =============================================================================

ROUND_NAMES = [
    'First Round (Round of 64)',
    'Second Round (Round of 32)',
    'Sweet 16',
    'Elite Eight',
    'Final Four',
    'Championship'
]

ROUND_SHORT_NAMES = {
    'First Round (Round of 64)': 'Round of 64',
    'Second Round (Round of 32)': 'Round of 32',
    'Sweet 16': 'Sweet 16',
    'Elite Eight': 'Elite 8',
    'Final Four': 'Final 4',
    'Championship': 'Championship'
}

ROUND_POINTS = {
    'First Round (Round of 64)': 2,
    'Second Round (Round of 32)': 4,
    'Sweet 16': 8,
    'Elite Eight': 16,
    'Final Four': 32,
    'Championship': 0  # Wager-based
}

# =============================================================================
# DATABASE MODELS
# =============================================================================

class User(db.Model, UserMixin):
    """User model for authentication and profile."""
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    is_admin = db.Column(db.Boolean, default=False)
    fun_name = db.Column(db.String(100), default='')
    picture = db.Column(db.String(100), default='default.png')
    bio = db.Column(db.String(500), default='')

    # Relationships
    picks = db.relationship('Pick', backref='user', lazy='dynamic')

    def set_password(self, password):
        """Hash and set the user's password."""
        self.password_hash = bcrypt.generate_password_hash(password).decode('utf-8')

    def check_password(self, password):
        """Check if the provided password matches the hash."""
        return bcrypt.check_password_hash(self.password_hash, password)

    def get_total_points(self):
        """Calculate total points across all closed rounds."""
        closed_round_ids = [r.id for r in Round.query.filter_by(closed=True).all()]
        if not closed_round_ids:
            return 0
        total = db.session.query(func.sum(Pick.points)).join(Game).filter(
            Pick.user_id == self.id,
            Game.round_id.in_(closed_round_ids)
        ).scalar()
        return total or 0

    def get_round_points(self, round_id):
        """Calculate points for a specific round."""
        total = db.session.query(func.sum(Pick.points)).join(Game).filter(
            Pick.user_id == self.id,
            Game.round_id == round_id
        ).scalar()
        return total or 0

    def get_correct_picks_count(self):
        """Get the number of correct picks."""
        return Pick.query.join(Game).filter(
            Pick.user_id == self.id,
            Pick.picked_team == Game.winner,
            Game.winner.isnot(None)
        ).count()

    def get_total_picks_count(self):
        """Get the total number of picks for games with results."""
        return Pick.query.join(Game).filter(
            Pick.user_id == self.id,
            Game.winner.isnot(None)
        ).count()

    def get_accuracy(self):
        """Get pick accuracy as a percentage."""
        total = self.get_total_picks_count()
        if total == 0:
            return 0
        return round((self.get_correct_picks_count() / total) * 100, 1)


class Round(db.Model):
    """Tournament round model."""
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(50), nullable=False, index=True)
    point_value = db.Column(db.Integer, default=2)
    closed = db.Column(db.Boolean, default=False, index=True)
    closed_for_selection = db.Column(db.Boolean, default=False)

    # Relationships
    games = db.relationship('Game', backref='round', lazy='dynamic', order_by='Game.id')

    @property
    def short_name(self):
        """Get short display name for the round."""
        return ROUND_SHORT_NAMES.get(self.name, self.name)

    @property
    def games_count(self):
        """Get the number of games in this round."""
        return self.games.count()

    @property
    def completed_games_count(self):
        """Get the number of games with winners set."""
        return self.games.filter(Game.winner.isnot(None)).count()

    @property
    def is_complete(self):
        """Check if all games in the round have winners."""
        return self.games_count > 0 and self.games_count == self.completed_games_count


class Game(db.Model):
    """Individual game/matchup model."""
    id = db.Column(db.Integer, primary_key=True)
    round_id = db.Column(db.Integer, db.ForeignKey('round.id'), nullable=False, index=True)
    team1 = db.Column(db.String(100), nullable=False)
    team2 = db.Column(db.String(100), nullable=False)
    winner = db.Column(db.String(100), nullable=True, index=True)
    team1_seed = db.Column(db.Integer, nullable=True)
    team2_seed = db.Column(db.Integer, nullable=True)
    region = db.Column(db.String(50), nullable=True)

    # Relationships
    picks = db.relationship('Pick', backref='game', lazy='dynamic')

    @property
    def display_name(self):
        """Get display name for the game."""
        seed1 = f"({self.team1_seed}) " if self.team1_seed else ""
        seed2 = f"({self.team2_seed}) " if self.team2_seed else ""
        return f"{seed1}{self.team1} vs {seed2}{self.team2}"

    def get_pick_distribution(self):
        """Get the distribution of picks for this game."""
        team1_count = self.picks.filter(Pick.picked_team == self.team1).count()
        team2_count = self.picks.filter(Pick.picked_team == self.team2).count()
        total = team1_count + team2_count
        return {
            'team1': {'count': team1_count, 'pct': round(team1_count/total*100) if total > 0 else 0},
            'team2': {'count': team2_count, 'pct': round(team2_count/total*100) if total > 0 else 0},
            'total': total
        }


class Pick(db.Model):
    """User pick for a specific game."""
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False, index=True)
    game_id = db.Column(db.Integer, db.ForeignKey('game.id'), nullable=False, index=True)
    picked_team = db.Column(db.String(100), nullable=False)
    wager = db.Column(db.Integer, default=0)
    points = db.Column(db.Integer, default=0)

    # Composite unique constraint
    __table_args__ = (
        db.UniqueConstraint('user_id', 'game_id', name='unique_user_game_pick'),
    )

    @property
    def is_correct(self):
        """Check if this pick is correct."""
        game = Game.query.get(self.game_id)
        return game and game.winner and self.picked_team == game.winner


# =============================================================================
# FLASK-LOGIN SETUP
# =============================================================================

@login_manager.user_loader
def load_user(user_id):
    """Load user for Flask-Login."""
    return User.query.get(int(user_id))


# =============================================================================
# CONTEXT PROCESSORS
# =============================================================================

@app.context_processor
def inject_globals():
    """Inject global template variables."""
    user_points = 0
    if current_user.is_authenticated:
        user_points = current_user.get_total_points()

    return {
        'user_points': user_points,
        'ROUND_SHORT_NAMES': ROUND_SHORT_NAMES,
        'current_year': datetime.now().year
    }


# =============================================================================
# HELPER FUNCTIONS
# =============================================================================

def calculate_points(round_obj):
    """Calculate and update points for all picks in a round."""
    games = Game.query.filter_by(round_id=round_obj.id).filter(Game.winner.isnot(None)).all()
    picks = Pick.query.filter(Pick.game_id.in_([g.id for g in games])).all()

    for pick in picks:
        game = Game.query.get(pick.game_id)
        if game and game.winner:
            if pick.picked_team == game.winner:
                if round_obj.name == 'Championship':
                    pick.points = pick.wager
                else:
                    pick.points = round_obj.point_value
            else:
                if round_obj.name == 'Championship':
                    pick.points = -pick.wager
                else:
                    pick.points = 0

    db.session.commit()
    logger.info(f"Calculated points for {len(picks)} picks in {round_obj.name}")


def create_next_round(current_round):
    """Create the next tournament round based on winners."""
    try:
        idx = ROUND_NAMES.index(current_round.name)
        if idx >= len(ROUND_NAMES) - 1:
            logger.warning("Cannot create round after Championship")
            return None

        next_round_name = ROUND_NAMES[idx + 1]
        point_value = ROUND_POINTS.get(next_round_name, current_round.point_value * 2)

        # Check if next round already exists
        existing = Round.query.filter_by(name=next_round_name).first()
        if existing:
            logger.warning(f"Round {next_round_name} already exists")
            return existing

        next_round = Round(
            name=next_round_name,
            point_value=point_value,
            closed=False,
            closed_for_selection=False
        )
        db.session.add(next_round)
        db.session.flush()  # Get the ID without committing

        # Create games from winners
        games = Game.query.filter_by(round_id=current_round.id).order_by(Game.id).all()
        for i in range(0, len(games), 2):
            team1 = games[i].winner if games[i].winner else "TBD"
            team2 = games[i + 1].winner if i + 1 < len(games) and games[i + 1].winner else "TBD"

            if team1 != "TBD" and team2 != "TBD":
                new_game = Game(round_id=next_round.id, team1=team1, team2=team2)
                db.session.add(new_game)

        db.session.commit()
        logger.info(f"Created round: {next_round_name}")
        return next_round

    except Exception as e:
        db.session.rollback()
        logger.error(f"Error creating next round: {e}")
        raise


def get_users_with_points():
    """Get all users sorted by total points."""
    users = User.query.all()
    for user in users:
        user.total_points = user.get_total_points()
        user.accuracy = user.get_accuracy()
    return sorted(users, key=lambda u: u.total_points, reverse=True)


def get_tournament_stats():
    """Get overall tournament statistics."""
    total_games = Game.query.count()
    completed_games = Game.query.filter(Game.winner.isnot(None)).count()
    total_picks = Pick.query.count()
    total_users = User.query.count()

    # Round completion stats
    rounds = Round.query.order_by(Round.id).all()
    round_stats = []
    for r in rounds:
        round_stats.append({
            'name': r.name,
            'short_name': r.short_name,
            'total_games': r.games_count,
            'completed': r.completed_games_count,
            'closed': r.closed,
            'point_value': r.point_value
        })

    return {
        'total_games': total_games,
        'completed_games': completed_games,
        'completion_pct': round(completed_games / total_games * 100) if total_games > 0 else 0,
        'total_picks': total_picks,
        'total_users': total_users,
        'rounds': round_stats
    }


# =============================================================================
# ROUTES - PUBLIC
# =============================================================================

@app.route('/')
def home():
    """Home page with tournament overview."""
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))

    # Get tournament stats for landing page
    stats = get_tournament_stats()
    current_round = Round.query.filter_by(closed=False).order_by(Round.id).first()
    top_users = get_users_with_points()[:3]

    return render_template('home.html',
                          stats=stats,
                          current_round=current_round,
                          top_users=top_users)


@app.route('/login', methods=['GET', 'POST'])
def login():
    """User login page."""
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))

    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')

        if not username or not password:
            flash('Please enter both username and password', 'warning')
            return render_template('login.html')

        user = User.query.filter_by(username=username).first()

        if user and user.check_password(password):
            login_user(user)
            logger.info(f"User {username} logged in")
            next_page = request.args.get('next')
            return redirect(next_page if next_page else url_for('dashboard'))
        else:
            flash('Invalid username or password', 'danger')
            logger.warning(f"Failed login attempt for {username}")

    return render_template('login.html')


@app.route('/logout')
@login_required
def logout():
    """User logout."""
    username = current_user.username
    logout_user()
    logger.info(f"User {username} logged out")
    flash('You have been logged out', 'info')
    return redirect(url_for('login'))


@app.route('/leaderboard')
def leaderboard():
    """Public leaderboard page."""
    users = get_users_with_points()
    stats = get_tournament_stats()
    return render_template('leaderboard.html', users=users, stats=stats)


# =============================================================================
# ROUTES - AUTHENTICATED
# =============================================================================

@app.route('/dashboard')
@login_required
def dashboard():
    """User dashboard with stats and quick actions."""
    users = get_users_with_points()
    stats = get_tournament_stats()

    # Find current user's rank
    current_rank = next((i + 1 for i, u in enumerate(users) if u.id == current_user.id), 0)

    # Get top 3 and last place
    top_3 = users[:3] if len(users) >= 3 else users
    last_place = users[-1] if users and len(users) > 3 else None

    # Get current open round
    current_round = Round.query.filter_by(closed=False, closed_for_selection=False).order_by(Round.id).first()

    # Check if user has made picks for current round
    has_picks = False
    if current_round:
        game_ids = [g.id for g in current_round.games]
        user_picks = Pick.query.filter(
            Pick.user_id == current_user.id,
            Pick.game_id.in_(game_ids)
        ).count()
        has_picks = user_picks == len(game_ids)

    return render_template('dashboard.html',
                          top_3=top_3,
                          last_place=last_place,
                          users=users,
                          stats=stats,
                          current_rank=current_rank,
                          current_round=current_round,
                          has_picks=has_picks)


@app.route('/pick', methods=['GET', 'POST'])
@login_required
def pick():
    """Make picks for the current round."""
    current_round = Round.query.filter_by(closed=False, closed_for_selection=False).order_by(Round.id).first()

    if not current_round:
        flash('No rounds are currently open for picks', 'warning')
        return redirect(url_for('dashboard'))

    games = Game.query.filter_by(round_id=current_round.id).order_by(Game.id).all()
    existing_picks = {
        p.game_id: p for p in Pick.query.filter(
            Pick.user_id == current_user.id,
            Pick.game_id.in_([g.id for g in games])
        ).all()
    }

    user_total_points = current_user.get_total_points()
    error_game_id = None
    wager = 0

    if request.method == 'POST':
        try:
            for game in games:
                picked_team = request.form.get(f'game{game.id}')

                if not picked_team or picked_team not in [game.team1, game.team2]:
                    error_game_id = game.id
                    wager = int(request.form.get('wager', 0)) if current_round.name == 'Championship' else 0
                    flash(f'Please select a team for {game.team1} vs {game.team2}', 'danger')
                    break

                existing_pick = existing_picks.get(game.id)
                if existing_pick:
                    existing_pick.picked_team = picked_team
                    if current_round.name == 'Championship':
                        wager = int(request.form.get('wager', existing_pick.wager))
                        existing_pick.wager = max(0, min(wager, user_total_points))
                else:
                    new_pick = Pick(user_id=current_user.id, game_id=game.id, picked_team=picked_team)
                    if current_round.name == 'Championship':
                        wager = int(request.form.get('wager', 0))
                        new_pick.wager = max(0, min(wager, user_total_points))
                    db.session.add(new_pick)

            if not error_game_id:
                db.session.commit()
                flash('Picks submitted successfully!', 'success')
                logger.info(f"User {current_user.username} submitted picks for {current_round.name}")
                return redirect(url_for('dashboard'))
            else:
                db.session.rollback()

        except Exception as e:
            db.session.rollback()
            logger.error(f"Error saving picks: {e}")
            flash(f'Error saving picks: {str(e)}', 'danger')

    return render_template('pick.html',
                          games=games,
                          existing_picks=existing_picks,
                          current_round=current_round,
                          user_points=user_total_points,
                          error_game_id=error_game_id,
                          wager=wager)


@app.route('/bracket')
@login_required
def bracket():
    """Interactive bracket visualization."""
    rounds = Round.query.order_by(Round.id).all()

    # Build bracket data structure
    bracket_data = []
    for r in rounds:
        games = Game.query.filter_by(round_id=r.id).order_by(Game.id).all()
        round_data = {
            'id': r.id,
            'name': r.name,
            'short_name': r.short_name,
            'closed': r.closed,
            'point_value': r.point_value,
            'games': []
        }
        for g in games:
            # Get current user's pick for this game
            user_pick = Pick.query.filter_by(user_id=current_user.id, game_id=g.id).first()
            game_data = {
                'id': g.id,
                'team1': g.team1,
                'team2': g.team2,
                'winner': g.winner,
                'team1_seed': g.team1_seed,
                'team2_seed': g.team2_seed,
                'region': g.region,
                'user_pick': user_pick.picked_team if user_pick else None,
                'distribution': g.get_pick_distribution() if r.closed else None
            }
            round_data['games'].append(game_data)
        bracket_data.append(round_data)

    return render_template('bracket.html', bracket_data=bracket_data, rounds=rounds)


@app.route('/view_picks')
@login_required
def view_picks():
    """View all picks for closed rounds."""
    closed_rounds = Round.query.filter_by(closed=True).order_by(Round.id.desc()).all()

    if not closed_rounds:
        flash('No closed rounds available to view', 'warning')
        return redirect(url_for('dashboard'))

    users = User.query.all()

    # Build data structure
    rounds_data = []
    for r in closed_rounds:
        games = Game.query.filter_by(round_id=r.id).order_by(Game.id).all()
        picks = Pick.query.options(
            joinedload(Pick.user),
            joinedload(Pick.game)
        ).filter(Pick.game_id.in_([g.id for g in games])).all()

        # Create picks lookup
        picks_lookup = {}
        for p in picks:
            if p.game_id not in picks_lookup:
                picks_lookup[p.game_id] = {}
            picks_lookup[p.game_id][p.user_id] = p

        # Calculate user totals for this round
        user_totals = []
        for user in users:
            total = sum(
                picks_lookup.get(g.id, {}).get(user.id, Pick(points=0)).points
                for g in games
            )
            user_totals.append({'user': user, 'total': total})
        user_totals.sort(key=lambda x: x['total'], reverse=True)

        rounds_data.append({
            'round': r,
            'games': games,
            'picks_lookup': picks_lookup,
            'user_totals': user_totals
        })

    return render_template('view_picks.html',
                          rounds_data=rounds_data,
                          users=users,
                          closed_rounds=closed_rounds)


@app.route('/stats')
@login_required
def stats():
    """Advanced statistics and analytics page."""
    users = get_users_with_points()
    tournament_stats = get_tournament_stats()

    # Build per-round stats for each user
    rounds = Round.query.filter_by(closed=True).order_by(Round.id).all()

    user_round_stats = []
    for user in users:
        stats_data = {
            'user': user,
            'total_points': user.total_points,
            'accuracy': user.accuracy,
            'rounds': {}
        }
        for r in rounds:
            stats_data['rounds'][r.id] = {
                'points': user.get_round_points(r.id),
                'name': r.short_name
            }
        user_round_stats.append(stats_data)

    # Upset stats - picks that went against popular opinion
    upset_picks = []
    for r in rounds:
        games = Game.query.filter_by(round_id=r.id).filter(Game.winner.isnot(None)).all()
        for g in games:
            dist = g.get_pick_distribution()
            # Find picks that were minority but correct
            minority_team = g.team1 if dist['team1']['pct'] < 50 else g.team2
            if g.winner == minority_team and dist['total'] > 0:
                minority_pct = dist['team1']['pct'] if minority_team == g.team1 else dist['team2']['pct']
                correct_picks = Pick.query.filter_by(game_id=g.id, picked_team=g.winner).all()
                for p in correct_picks:
                    upset_picks.append({
                        'user': User.query.get(p.user_id),
                        'game': g,
                        'round': r,
                        'minority_pct': minority_pct
                    })

    # Sort by how contrarian the pick was
    upset_picks.sort(key=lambda x: x['minority_pct'])

    return render_template('stats.html',
                          users=users,
                          tournament_stats=tournament_stats,
                          rounds=rounds,
                          user_round_stats=user_round_stats,
                          upset_picks=upset_picks[:10])


@app.route('/profile/<username>')
@login_required
def profile(username):
    """User profile page."""
    user = User.query.filter_by(username=username).first_or_404()

    # Get user stats
    total_points = user.get_total_points()
    accuracy = user.get_accuracy()
    correct_picks = user.get_correct_picks_count()
    total_picks = user.get_total_picks_count()

    # Get rank
    users = get_users_with_points()
    rank = next((i + 1 for i, u in enumerate(users) if u.id == user.id), 0)

    # Get picks by round
    rounds = Round.query.filter_by(closed=True).order_by(Round.id).all()
    picks_by_round = []
    for r in rounds:
        games = Game.query.filter_by(round_id=r.id).order_by(Game.id).all()
        round_picks = []
        for g in games:
            pick = Pick.query.filter_by(user_id=user.id, game_id=g.id).first()
            round_picks.append({
                'game': g,
                'pick': pick,
                'correct': pick.is_correct if pick else None
            })
        picks_by_round.append({
            'round': r,
            'picks': round_picks,
            'points': user.get_round_points(r.id)
        })

    return render_template('profile.html',
                          profile_user=user,
                          total_points=total_points,
                          accuracy=accuracy,
                          correct_picks=correct_picks,
                          total_picks=total_picks,
                          rank=rank,
                          total_users=len(users),
                          picks_by_round=picks_by_round)


@app.route('/profile/<username>/edit', methods=['GET', 'POST'])
@login_required
def edit_profile(username):
    """Edit user's own profile."""
    user = User.query.filter_by(username=username).first_or_404()

    # Only allow users to edit their own profile
    if user.id != current_user.id:
        flash('You can only edit your own profile', 'danger')
        return redirect(url_for('profile', username=username))

    if request.method == 'POST':
        bio = request.form.get('bio', '').strip()
        fun_name = request.form.get('fun_name', '').strip()

        user.bio = bio[:500]  # Limit to 500 chars
        user.fun_name = fun_name[:100]  # Limit to 100 chars

        db.session.commit()
        flash('Profile updated successfully!', 'success')
        logger.info(f"User {username} updated their profile")
        return redirect(url_for('profile', username=username))

    return render_template('edit_profile.html', profile_user=user)


# =============================================================================
# ROUTES - ADMIN
# =============================================================================

@app.route('/admin', methods=['GET', 'POST'])
@login_required
def admin():
    """Admin panel for managing rounds and games."""
    if not current_user.is_admin:
        flash('Access denied', 'danger')
        return redirect(url_for('dashboard'))

    all_rounds = Round.query.order_by(Round.id).all()
    selected_round_id = request.args.get('round_id', type=int) or (all_rounds[-1].id if all_rounds else None)
    selected_round = Round.query.get(selected_round_id) if selected_round_id else None

    if request.method == 'POST':
        round_id = request.form.get('round_id', type=int)
        if round_id:
            selected_round = Round.query.get(round_id)
            if selected_round:
                # Update game winners
                for game in selected_round.games:
                    winner = request.form.get(f'game{game.id}_winner')
                    if selected_round.name == 'First Round (Round of 64)':
                        if winner in [game.team1, game.team2]:
                            game.winner = winner
                        elif winner == '':
                            game.winner = None
                    else:
                        team1 = request.form.get(f'game{game.id}_team1_select')
                        team2 = request.form.get(f'game{game.id}_team2_select')

                        if team1:
                            game.team1 = team1
                        if team2:
                            game.team2 = team2
                        if winner in [game.team1, game.team2]:
                            game.winner = winner
                        elif winner == '':
                            game.winner = None

                # Update round status
                selected_round.closed = request.form.get('closed') == 'on'
                selected_round.closed_for_selection = selected_round.closed

                # Update point value
                try:
                    points = int(request.form.get('point_value', selected_round.point_value))
                    if points > 0:
                        selected_round.point_value = points
                except ValueError:
                    pass

                # Create next round if requested
                if 'next_round' in request.form and selected_round.name != 'Championship':
                    if selected_round.is_complete:
                        selected_round.closed = True
                        selected_round.closed_for_selection = True
                        db.session.commit()
                        calculate_points(selected_round)
                        next_round = create_next_round(selected_round)
                        if next_round:
                            flash(f'Next round ({next_round.name}) created!', 'success')
                            return redirect(url_for('admin', round_id=next_round.id))
                    else:
                        flash('Cannot create next round: All winners must be set', 'danger')

                db.session.commit()
                calculate_points(selected_round)
                flash(f'{selected_round.name} saved successfully!', 'success')

        return redirect(url_for('admin', round_id=selected_round_id))

    # Get previous round winners for dropdown
    prev_winners = []
    if selected_round and selected_round.name != 'First Round (Round of 64)':
        idx = ROUND_NAMES.index(selected_round.name)
        if idx > 0:
            prev_round = Round.query.filter_by(name=ROUND_NAMES[idx - 1]).first()
            if prev_round:
                prev_winners = [g.winner for g in prev_round.games if g.winner]

    # Get users pick status
    users_with_picks = []
    if selected_round:
        game_ids = [g.id for g in selected_round.games]
        for user in User.query.all():
            user_picks = Pick.query.filter(
                Pick.user_id == user.id,
                Pick.game_id.in_(game_ids)
            ).count()
            users_with_picks.append({
                'user': user,
                'picks_count': user_picks,
                'has_all_picks': user_picks == len(game_ids)
            })

    return render_template('admin.html',
                          all_rounds=all_rounds,
                          selected_round=selected_round,
                          prev_winners=prev_winners,
                          users_with_picks=users_with_picks)


@app.route('/admin_submit_picks', methods=['GET', 'POST'])
@login_required
def admin_submit_picks():
    """Admin page to submit picks on behalf of users."""
    if not current_user.is_admin:
        flash('Access denied', 'danger')
        return redirect(url_for('dashboard'))

    all_open_rounds = Round.query.filter_by(closed=False).order_by(Round.id).all()
    selected_round_id = (request.form.get('round_id', type=int) or
                        request.args.get('round_id', type=int) or
                        (all_open_rounds[0].id if all_open_rounds else None))
    current_round = Round.query.get(selected_round_id) if selected_round_id and all_open_rounds else None

    if not current_round:
        flash('No open rounds available', 'warning')
        return redirect(url_for('admin'))

    users = User.query.order_by(User.username).all()
    games = Game.query.filter_by(round_id=current_round.id).order_by(Game.id).all()

    selected_user_id = (request.form.get('user_id', type=int) if request.method == 'POST'
                       else request.args.get('user_id', type=int))
    selected_user = User.query.get(selected_user_id) if selected_user_id else None

    selected_user_points = selected_user.get_total_points() if selected_user else 0

    existing_picks = {}
    if selected_user:
        existing_picks = {
            p.game_id: p for p in Pick.query.filter(
                Pick.user_id == selected_user.id,
                Pick.game_id.in_([g.id for g in games])
            ).all()
        }

    error_game_id = None
    wager = 0

    if request.method == 'POST' and 'submit_picks' in request.form and selected_user:
        try:
            for game in games:
                picked_team = request.form.get(f'game{game.id}')

                if not picked_team or picked_team not in [game.team1, game.team2]:
                    error_game_id = game.id
                    wager = int(request.form.get('wager', 0)) if current_round.name == 'Championship' else 0
                    break

                existing_pick = existing_picks.get(game.id)
                if existing_pick:
                    existing_pick.picked_team = picked_team
                    if current_round.name == 'Championship':
                        wager = int(request.form.get('wager', existing_pick.wager))
                        existing_pick.wager = max(0, min(wager, selected_user_points))
                else:
                    new_pick = Pick(user_id=selected_user.id, game_id=game.id, picked_team=picked_team)
                    if current_round.name == 'Championship':
                        wager = int(request.form.get('wager', 0))
                        new_pick.wager = max(0, min(wager, selected_user_points))
                    db.session.add(new_pick)

            if not error_game_id:
                db.session.commit()
                flash(f'Picks submitted for {selected_user.username}!', 'success')
                logger.info(f"Admin submitted picks for {selected_user.username}")
                return redirect(url_for('admin'))
            else:
                db.session.rollback()

        except Exception as e:
            db.session.rollback()
            logger.error(f"Error saving picks: {e}")
            flash(f'Error saving picks: {str(e)}', 'danger')

    return render_template('admin_submit_picks.html',
                          all_open_rounds=all_open_rounds,
                          current_round=current_round,
                          games=games,
                          users=users,
                          existing_picks=existing_picks,
                          selected_user_id=selected_user_id,
                          selected_user=selected_user,
                          selected_user_points=selected_user_points,
                          error_game_id=error_game_id,
                          wager=wager)


# =============================================================================
# API ENDPOINTS
# =============================================================================

@app.route('/api/leaderboard')
def api_leaderboard():
    """API endpoint for leaderboard data."""
    users = get_users_with_points()
    return jsonify([{
        'rank': i + 1,
        'username': u.username,
        'fun_name': u.fun_name,
        'points': u.total_points,
        'accuracy': u.accuracy,
        'picture': url_for('static', filename=u.picture)
    } for i, u in enumerate(users)])


@app.route('/api/stats')
def api_stats():
    """API endpoint for tournament statistics."""
    return jsonify(get_tournament_stats())


@app.route('/api/bracket')
def api_bracket():
    """API endpoint for bracket data."""
    rounds = Round.query.order_by(Round.id).all()
    bracket = []

    for r in rounds:
        games = Game.query.filter_by(round_id=r.id).order_by(Game.id).all()
        bracket.append({
            'id': r.id,
            'name': r.name,
            'short_name': r.short_name,
            'closed': r.closed,
            'point_value': r.point_value,
            'games': [{
                'id': g.id,
                'team1': g.team1,
                'team2': g.team2,
                'winner': g.winner,
                'team1_seed': g.team1_seed,
                'team2_seed': g.team2_seed
            } for g in games]
        })

    return jsonify(bracket)


@app.route('/api/user/<username>/picks')
@login_required
def api_user_picks(username):
    """API endpoint for user's picks."""
    user = User.query.filter_by(username=username).first_or_404()

    picks_data = []
    for pick in user.picks:
        game = Game.query.get(pick.game_id)
        round_obj = Round.query.get(game.round_id)
        picks_data.append({
            'game_id': game.id,
            'round': round_obj.name,
            'matchup': f"{game.team1} vs {game.team2}",
            'picked_team': pick.picked_team,
            'winner': game.winner,
            'correct': pick.is_correct,
            'points': pick.points
        })

    return jsonify({
        'username': user.username,
        'total_points': user.get_total_points(),
        'accuracy': user.get_accuracy(),
        'picks': picks_data
    })


# =============================================================================
# ERROR HANDLERS
# =============================================================================

@app.errorhandler(404)
def not_found_error(error):
    """Handle 404 errors."""
    return render_template('errors/404.html'), 404


@app.errorhandler(500)
def internal_error(error):
    """Handle 500 errors."""
    db.session.rollback()
    logger.error(f"Internal error: {error}")
    return render_template('errors/500.html'), 500


# =============================================================================
# DATABASE INITIALIZATION
# =============================================================================

def init_db():
    """Initialize the database."""
    with app.app_context():
        db.create_all()
        logger.info("Database tables created")


# =============================================================================
# MAIN
# =============================================================================

if __name__ == '__main__':
    init_db()
    app.run(debug=os.getenv('FLASK_ENV') == 'development')
