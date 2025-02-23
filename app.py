import os
from flask import Flask, render_template, redirect, url_for, request, flash
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, logout_user, current_user, login_required
from flask_bcrypt import Bcrypt

app = Flask(__name__)

# Load the secret key from a file
try:
    with open('secret_key.txt', 'r') as f:
        app.config['SECRET_KEY'] = f.read().strip()
except FileNotFoundError:
    raise Exception("Error: secret_key.txt not found in project directory. Please create it with a secure key.")

# Use environment variable for database URI, with SQLite as fallback
app.config['SQLALCHEMY_DATABASE_URI'] = os.getenv('DATABASE_URL', 'sqlite:///mm2025.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

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
    current_round = Round.query.filter_by(closed=False).order_by(Round.id).first()
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
        for pick in picks:
            db.session.delete(pick)
        db.session.commit()
        for game in games:
            picked_team = request.form.get(f'game{game.id}')
            if not picked_team or picked_team not in [game.team1, game.team2]:
                flash('Invalid or missing selection for a game.', 'danger')
                db.session.rollback()
                return redirect(url_for('pick'))
            pick = Pick(user_id=current_user.id, game_id=game.id, picked_team=picked_team)
            if current_round.name == 'Championship':
                try:
                    wager = int(request.form.get('wager', 0))
                    if wager < 0:
                        wager = 0
                    if wager > current_user.points:
                        wager = current_user.points
                    pick.wager = wager
                except ValueError:
                    pick.wager = 0
            db.session.add(pick)
        db.session.commit()
        flash('Picks submitted successfully!', 'success')
        return redirect(url_for('home'))
    return render_template('pick.html', games=games, existing_picks=existing_picks, current_round=current_round, prev_winners=prev_winners if current_round.name != 'First Round (Round of 64)' else None)

@app.route('/view_picks')
@login_required
def view_picks():
    closed_rounds = Round.query.filter_by(closed=True).all()
    if not closed_rounds:
        flash('No closed rounds available to view picks', 'warning')
        return redirect(url_for('home'))
    # Recalculate total points for all users
    users = User.query.all()
    for user in users:
        user.points = 0  # Reset points
    for round in closed_rounds:
        calculate_points(round)
    db.session.commit()
    return render_template('view_picks.html', closed_rounds=closed_rounds)

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

    return render_template('admin.html', all_rounds=all_rounds, selected_round=selected_round, prev_winners=prev_winners, users_with_picks=users_with_picks)

@app.route('/leaderboard')
def leaderboard():
    users = User.query.order_by(User.points.desc()).all()
    return render_template('leaderboard.html', users=users)

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

# Export Flask app for Vercel
app = app