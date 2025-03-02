import os
from flask import Flask, render_template, redirect, url_for, request, flash
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, logout_user, current_user, login_required
from flask_bcrypt import Bcrypt
import logging
import time
from sqlalchemy.orm import joinedload

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
    picks = db.relationship('Pick', backref='game_instance')

class Pick(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    game_id = db.Column(db.Integer, db.ForeignKey('game.id'), nullable=False)
    picked_team = db.Column(db.String(100), nullable=False)
    wager = db.Column(db.Integer, default=0)
    points = db.Column(db.Integer, default=0)
    user = db.relationship('User', backref='picks')
    game = db.relationship('Game', backref='game_picks')

# Helper Functions
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
    return next_round

# Context Processor for Navbar Points
@app.context_processor
def inject_user_points():
    if current_user.is_authenticated:
        closed_round_ids = [r.id for r in Round.query.filter_by(closed=True).all()]
        total_points = db.session.query(db.func.sum(Pick.points)).join(Game).filter(Pick.user_id == current_user.id, Game.round_id.in_(closed_round_ids)).scalar() or 0
        return {'user_points': total_points}
    return {'user_points': 0}

# Routes
@app.route('/')
def home():
    all_rounds_complete = not Round.query.join(Game).filter(Game.winner.is_(None)).first()
    if all_rounds_complete:
        users = User.query.all()
        closed_round_ids = [r.id for r in Round.query.filter_by(closed=True).all()]
        for user in users:
            total_points = db.session.query(db.func.sum(Pick.points)).join(Game).filter(Pick.user_id == user.id, Game.round_id.in_(closed_round_ids)).scalar() or 0
            user.points = total_points
        users = sorted(users, key=lambda u: u.points, reverse=True)
        return render_template('leaderboard.html', users=users, final=True)
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))
    current_round = Round.query.filter_by(closed=False).order_by(Round.id).first()
    return render_template('home.html', current_round=current_round)

@app.route('/dashboard')
@login_required
def dashboard():
    users = User.query.all()
    closed_round_ids = [r.id for r in Round.query.filter_by(closed=True).all()]
    for user in users:
        total_points = db.session.query(db.func.sum(Pick.points)).join(Game).filter(Pick.user_id == user.id, Game.round_id.in_(closed_round_ids)).scalar() or 0
        user.points = total_points
    if users:
        sorted_users = sorted(users, key=lambda u: u.points, reverse=True)
        all_scores = [user.points for user in sorted_users]
        if len(set(all_scores)) == 1:
            winner = None
            loser = None
        else:
            winner = sorted_users[0]
            loser = sorted_users[-1]
    else:
        winner = None
        loser = None
    return render_template('dashboard.html', winner=winner, loser=loser)

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
    return redirect(url_for('login'))

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
                    wager = int(request.form.get('wager', 0)) if current_round.name == 'Championship' else 0
                    break
                else:
                    existing_pick = existing_picks.get(game.id)
                    if existing_pick:
                        existing_pick.picked_team = picked_team
                        if current_round.name == 'Championship':
                            wager = int(request.form.get('wager', existing_pick.wager))
                            existing_pick.wager = max(0, min(wager, user_total_points))
                    else:
                        pick = Pick(user_id=current_user.id, game_id=game.id, picked_team=picked_team)
                        if current_round.name == 'Championship':
                            wager = int(request.form.get('wager', 0))
                            pick.wager = max(0, min(wager, user_total_points))
                        db.session.add(pick)

            if error_game_id:
                return render_template('pick.html', games=games, existing_picks=existing_picks, current_round=current_round,
                                       user_points=user_total_points, error_game_id=error_game_id, wager=wager)

            db.session.commit()
            flash('Picks submitted successfully! Points will update on next leaderboard view.', 'success')
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
        flash('Access denied', 'error')
        return redirect(url_for('home'))
    all_rounds = Round.query.order_by(Round.id).all()
    selected_round_id = request.args.get('round_id', type=int) or (all_rounds[-1].id if all_rounds else None)
    selected_round = Round.query.get(selected_round_id) if selected_round_id else None

    # Check if the user is Chris for the prank
    is_chris = current_user.username.lower() == 'chris'

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
                        flash('Cannot create next round: All winners must be set', 'error')
                
                db.session.commit()
                calculate_points(selected_round)
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

    return render_template('admin.html', all_rounds=all_rounds, selected_round=selected_round, prev_winners=prev_winners, users_with_picks=users_with_picks, is_chris=is_chris)

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
                    wager = int(request.form.get('wager', 0)) if current_round.name == 'Championship' else 0
                    break
                else:
                    existing_pick = Pick.query.filter_by(user_id=selected_user.id, game_id=game.id).first()
                    if existing_pick:
                        existing_pick.picked_team = picked_team
                        if current_round.name == 'Championship':
                            wager = int(request.form.get('wager', existing_pick.wager))
                            existing_pick.wager = max(0, min(wager, selected_user_points))
                    else:
                        pick = Pick(user_id=selected_user.id, game_id=game.id, picked_team=picked_team)
                        if current_round.name == 'Championship':
                            wager = int(request.form.get('wager', 0))
                            pick.wager = max(0, min(wager, selected_user_points))
                        db.session.add(pick)
            
            if error_game_id:
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
    users = User.query.all()
    closed_round_ids = [r.id for r in Round.query.filter_by(closed=True).all()]
    for user in users:
        total_points = db.session.query(db.func.sum(Pick.points)).join(Game).filter(Pick.user_id == user.id, Game.round_id.in_(closed_round_ids)).scalar() or 0
        user.points = total_points
    users = sorted(users, key=lambda u: u.points, reverse=True)
    return render_template('leaderboard.html', users=users)

if __name__ == '__main__':
    app.run(debug=True)