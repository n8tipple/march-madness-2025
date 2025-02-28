import os
from flask import Flask, render_template, redirect, url_for, request, flash
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, logout_user, current_user, login_required
from flask_bcrypt import Bcrypt
from flask_caching import Cache  # Added for caching
import logging

app = Flask(__name__)

try:
    with open('secret_key.txt', 'r') as f:
        app.config['SECRET_KEY'] = f.read().strip()
except FileNotFoundError:
    raise Exception("Error: secret_key.txt not found in project directory. Please create it with a secure key.")

app.config['SQLALCHEMY_DATABASE_URI'] = os.getenv('DATABASE_URL', 'postgresql://postgres.klggxmynqzhtoxoalngl:U%40FTA_mZhava.6y@aws-0-us-west-1.pooler.supabase.com:6543/postgres')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# Set up logging
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

# Configure cache
cache = Cache(app, config={'CACHE_TYPE': 'simple'})  # Added for caching

db = SQLAlchemy(app)
bcrypt = Bcrypt(app)
login_manager = LoginManager(app)
login_manager.login_view = 'login'

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

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
    picks = db.relationship('Pick', backref='game')

class Pick(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    game_id = db.Column(db.Integer, db.ForeignKey('game.id'), nullable=False)
    picked_team = db.Column(db.String(100), nullable=False)
    wager = db.Column(db.Integer, default=0)
    user = db.relationship('User', backref='picks')

# Helper Functions
def calculate_points(round):
    games = Game.query.filter_by(round_id=round.id).filter(Game.winner.isnot(None)).all()
    picks = Pick.query.filter(Pick.game_id.in_([g.id for g in games])).all()
    for pick in picks:
        game = Game.query.get(pick.game_id)
        if game.winner:
            if pick.picked_team == game.winner:
                if round.name == 'Championship':
                    pick.user.points += pick.wager
                else:
                    pick.user.points += round.point_value
            elif round.name == 'Championship' and game.winner:
                pick.user.points -= pick.wager
    db.session.commit()

def recalculate_all_points():
    users = User.query.all()
    for user in users:
        user.points = 0
    db.session.commit()
    closed_rounds = Round.query.filter_by(closed=True).all()
    for round in closed_rounds:
        calculate_points(round)
    db.session.commit()

def create_next_round(current_round):
    round_names = ['First Round (Round of 64)', 'Second Round (Round of 32)', 'Sweet 16', 'Elite Eight', 'Final Four', 'Championship']
    idx = round_names.index(current_round.name)
    next_round_name = round_names[idx + 1]
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

# Routes
@app.route('/')
def home():
    all_rounds_complete = not Round.query.join(Game).filter(Game.winner.is_(None)).first()
    if all_rounds_complete:
        users = User.query.order_by(User.points.desc()).all()
        return render_template('leaderboard.html', users=users, final=True)
    current_round = Round.query.filter_by(closed=False).order_by(Round.id).first()
    return render_template('home.html', current_round=current_round)

@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('home'))
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        user = User.query.filter_by(username=username).first()
        if user and user.check_password(password):
            login_user(user)
            return redirect(url_for('home'))
        else:
            flash('Invalid username or password', 'danger')
    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('login'))

@app.route('/pick', methods=['GET', 'POST'])
@login_required
def pick():
    all_open_rounds = Round.query.filter_by(closed=False).order_by(Round.id).all()
    selected_round_id = request.args.get('round_id', type=int) or (all_open_rounds[0].id if all_open_rounds else None)
    current_round = Round.query.get(selected_round_id) if selected_round_id and all_open_rounds else None
    if not current_round:
        flash('No open rounds available for picks', 'warning')
        return redirect(url_for('home'))
    games = Game.query.filter_by(round_id=current_round.id).all()
    picks = Pick.query.filter(Pick.user_id == current_user.id, Pick.game_id.in_([g.id for g in games])).all()
    existing_picks = {pick.game_id: pick for pick in picks}
    prev_round = None
    if current_round.name != 'First Round (Round of 64)':
        round_names = ['First Round (Round of 64)', 'Second Round (Round of 32)', 'Sweet 16', 'Elite Eight', 'Final Four', 'Championship']
        prev_round_idx = round_names.index(current_round.name) - 1
        prev_round = Round.query.filter_by(name=round_names[prev_round_idx]).first()
        prev_winners = [game.winner for game in prev_round.games if game.winner] if prev_round else []

    if request.method == 'POST':
        try:
            for game in games:
                picked_team = request.form.get(f'game{game.id}')
                if not picked_team or picked_team not in [game.team1, game.team2]:
                    flash('Invalid or missing selection for a game.', 'danger')
                    db.session.rollback()
                    return redirect(url_for('pick'))

                existing_pick = existing_picks.get(game.id)
                if existing_pick:
                    existing_pick.picked_team = picked_team
                    if current_round.name == 'Championship':
                        try:
                            wager = int(request.form.get('wager', existing_pick.wager))
                            wager = max(0, min(wager, current_user.points))
                            existing_pick.wager = wager
                        except ValueError:
                            pass
                else:
                    pick = Pick(user_id=current_user.id, game_id=game.id, picked_team=picked_team)
                    if current_round.name == 'Championship':
                        try:
                            wager = int(request.form.get('wager', 0))
                            wager = max(0, min(wager, current_user.points))
                            pick.wager = wager
                        except ValueError:
                            pick.wager = 0
                    db.session.add(pick)

            db.session.commit()
            recalculate_all_points()
            flash('Picks submitted successfully!', 'success')
            return redirect(url_for('home'))
        except Exception as e:
            db.session.rollback()
            flash(f'Error saving picks: {str(e)}', 'danger')
            return redirect(url_for('pick'))

    return render_template('pick.html', all_open_rounds=all_open_rounds, games=games, existing_picks=existing_picks, current_round=current_round, prev_winners=prev_winners if current_round.name != 'First Round (Round of 64)' else None)

@app.route('/view_picks')
@cache.cached(timeout=300)  # Cache for 5 minutes
@login_required
def view_picks():
    # Fetch closed rounds once, ordered by id descending (most recent first)
    closed_rounds = Round.query.filter_by(closed=True).order_by(Round.id.desc()).all()
    if not closed_rounds:
        flash('No closed rounds available to view picks', 'warning')
        return redirect(url_for('home'))

    # Fetch all users once
    users = User.query.all()

    # Get all game IDs for closed rounds in one query
    closed_round_ids = [round.id for round in closed_rounds]
    games = Game.query.filter(Game.round_id.in_(closed_round_ids)).all()
    game_ids = [game.id for game in games]

    # Fetch all picks for these games in one query
    picks = Pick.query.filter(Pick.game_id.in_(game_ids)).all()

    # Precompute game-to-picks mapping for efficiency
    picks_by_game = {}
    for pick in picks:
        if pick.game_id not in picks_by_game:
            picks_by_game[pick.game_id] = []
        picks_by_game[pick.game_id].append(pick)

    # Reset points and recalculate only once
    for user in users:
        user.points = 0
    for round in closed_rounds:
        calculate_points(round)
    db.session.commit()

    # Organize games by round efficiently
    games_by_round = {round.id: [g for g in games if g.round_id == round.id] for round in closed_rounds}
    for round_id, round_games in games_by_round.items():
        logger.debug(f"Round {round_id} has {len(round_games)} games")
        for game in round_games:
            game.picks = picks_by_game.get(game.id, [])

    # Precompute points and totals
    points_by_user_game = {}
    user_totals_by_round = {}
    for round in closed_rounds:
        points_by_user_game[round.id] = {user.id: {} for user in users}
        user_totals_by_round[round.id] = {user.id: 0 for user in users}
        for game in games_by_round[round.id]:
            for user in users:
                pick = next((p for p in picks_by_game.get(game.id, []) if p.user_id == user.id), None)
                points = 0
                if game.winner and pick:
                    if pick.picked_team == game.winner:
                        points = pick.wager if round.name == 'Championship' else round.point_value
                    elif round.name == 'Championship':
                        points = -pick.wager
                points_by_user_game[round.id][user.id][game.id] = points
                user_totals_by_round[round.id][user.id] += points

    # Sort users by total points per round
    for round_id in user_totals_by_round:
        user_totals = [(user, user_totals_by_round[round_id][user.id]) for user in users]
        user_totals_by_round[round_id] = sorted(user_totals, key=lambda x: x[1], reverse=True)

    return render_template('view_picks.html', closed_rounds=closed_rounds, users=users,
                          games_by_round=games_by_round, points_by_user_game=points_by_user_game,
                          user_totals_by_round=user_totals_by_round)

@app.route('/admin', methods=['GET', 'POST'])
@login_required
def admin():
    if not current_user.is_admin:
        flash('Access denied', 'error')
        return redirect(url_for('home'))
    all_rounds = Round.query.order_by(Round.id).all()
    selected_round_id = request.args.get('round_id', type=int) or (all_rounds[-1].id if all_rounds else None)
    selected_round = Round.query.get(selected_round_id) if selected_round_id else None

    if request.method == 'POST':
        round_id = request.form.get('round_id', type=int)
        if round_id:
            selected_round = Round.query.get(round_id)
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
                        round_names = ['First Round (Round of 64)', 'Second Round (Round of 32)', 'Sweet 16', 'Elite Eight', 'Final Four', 'Championship']
                        prev_round_idx = round_names.index(selected_round.name) - 1
                        prev_round = Round.query.filter_by(name=round_names[prev_round_idx]).first()
                        prev_winners = [game.winner for game in prev_round.games if game.winner] if prev_round else []
                        if team1 in prev_winners and team2 in prev_winners and team1 != team2:
                            game.team1 = team1
                            game.team2 = team2
                            if winner in [team1, team2]:
                                game.winner = winner
                            else:
                                game.winner = None
                        else:
                            flash(f'Invalid teams selected for {game.team1} vs {game.team2}', 'error')
                
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
                        round_names = ['First Round (Round of 64)', 'Second Round (Round of 32)', 'Sweet 16', 'Elite Eight', 'Final Four', 'Championship']
                        next_round_name = round_names[round_names.index(selected_round.name) + 1]
                        if not Round.query.filter_by(name=next_round_name).first():
                            next_round = Round(
                                name=next_round_name,
                                point_value=selected_round.point_value * 2 if next_round_name != 'Championship' else selected_round.point_value,
                                closed=True,
                                closed_for_selection=True
                            )
                            db.session.add(next_round)
                            db.session.commit()
                            games = Game.query.filter_by(round_id=selected_round.id).order_by(Game.id).all()
                            for i in range(0, len(games), 2):
                                team1 = games[i].winner
                                team2 = games[i + 1].winner if i + 1 < len(games) else None
                                if team1 and team2:
                                    db.session.add(Game(round_id=next_round.id, team1=team1, team2=team2))
                            db.session.commit()
                            flash(f'Next round ({next_round_name}) created', 'success')
                            return redirect(url_for('admin', round_id=next_round.id))
                        else:
                            flash(f'{next_round_name} already exists', 'warning')
                    else:
                        flash('Cannot create next round: All winners must be set', 'error')
                
                db.session.commit()
                recalculate_all_points()
                flash(f'{selected_round.name} saved successfully', 'success')
        return redirect(url_for('admin', round_id=selected_round_id))
    
    prev_round = None
    if selected_round and selected_round.name != 'First Round (Round of 64)':
        round_names = ['First Round (Round of 64)', 'Second Round (Round of 32)', 'Sweet 16', 'Elite Eight', 'Final Four', 'Championship']
        prev_round_idx = round_names.index(selected_round.name) - 1
        prev_round = Round.query.filter_by(name=round_names[prev_round_idx]).first()
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

    return render_template('admin.html', all_rounds=all_rounds, selected_round=selected_round, prev_winners=prev_winners, users_with_picks=users_with_picks)

@app.route('/admin_submit_picks', methods=['GET', 'POST'])
@login_required
def admin_submit_picks():
    if not current_user.is_admin:
        flash('Access denied', 'error')
        return redirect(url_for('home'))
    
    all_open_rounds = Round.query.filter_by(closed=False).order_by(Round.id).all()
    selected_round_id = request.form.get('round_id', type=int) or request.args.get('round_id', type=int) or (all_open_rounds[0].id if all_open_rounds else None)
    current_round = Round.query.get(selected_round_id) if selected_round_id and all_open_rounds else None
    users = User.query.all()
    
    if not current_round:
        flash('No open rounds available for picks', 'warning')
        return redirect(url_for('admin'))
    
    games = Game.query.filter_by(round_id=current_round.id).all()
    
    selected_user_id = request.form.get('user_id', type=int) if request.method == 'POST' else request.args.get('user_id', type=int)
    selected_user = User.query.get(selected_user_id) if selected_user_id else None
    
    existing_picks = {}
    if selected_user:
        picks = Pick.query.filter(Pick.user_id == selected_user.id, Pick.game_id.in_([g.id for g in games])).all()
        existing_picks = {pick.game_id: pick for pick in picks}
    
    if request.method == 'POST' and 'submit_picks' in request.form and selected_user:
        try:
            for game in games:
                picked_team = request.form.get(f'game{game.id}')
                if not picked_team or picked_team not in [game.team1, game.team2]:
                    flash('Invalid or missing selection for a game.', 'danger')
                    db.session.rollback()
                    return redirect(url_for('admin_submit_picks', round_id=current_round.id, user_id=selected_user_id))
                
                existing_pick = Pick.query.filter_by(user_id=selected_user.id, game_id=game.id).first()
                if existing_pick:
                    existing_pick.picked_team = picked_team
                    if current_round.name == 'Championship':
                        try:
                            wager = int(request.form.get('wager', existing_pick.wager))
                            wager = max(0, min(wager, selected_user.points))
                            existing_pick.wager = wager
                        except ValueError:
                            pass
                else:
                    pick = Pick(user_id=selected_user.id, game_id=game.id, picked_team=picked_team)
                    if current_round.name == 'Championship':
                        try:
                            wager = int(request.form.get('wager', 0))
                            wager = max(0, min(wager, selected_user.points))
                            pick.wager = wager
                        except ValueError:
                            pick.wager = 0
                    db.session.add(pick)
            
            db.session.commit()
            recalculate_all_points()
            flash(f'Picks submitted successfully for {selected_user.username}!', 'success')
            return redirect(url_for('admin'))
        except Exception as e:
            db.session.rollback()
            flash(f'Error saving picks: {str(e)}', 'danger')
            return redirect(url_for('admin_submit_picks', round_id=current_round.id, user_id=selected_user_id))
    
    return render_template('admin_submit_picks.html', all_open_rounds=all_open_rounds, current_round=current_round, games=games, users=users, existing_picks=existing_picks, selected_user_id=selected_user_id, selected_user=selected_user)

@app.route('/leaderboard')
def leaderboard():
    recalculate_all_points()
    users = User.query.order_by(User.points.desc()).all()
    return render_template('leaderboard.html', users=users)

if __name__ == '__main__':
    app.run(debug=True)