from app import app, db, User, Round, Game

with app.app_context():
    # Drop all tables to start fresh
    db.drop_all()
    # Recreate all tables
    db.create_all()

    # List of users with fixed passwords, admin status, and fun names
    users = [
        ('June', 'jump27', False, 'Slam Dunk June'),
        ('Don', 'shot28', False, 'Dribble Don'),
        ('Nate', 'pass57', True, 'Nothin’ But Nate'),
        ('Chris', 'pass18', True, 'Crossover Chris'),
        ('Casey', 'ball32', True, 'Three-Point Casey'),
        ('James', 'slam56', False, 'Jumpin’ James'),
        ('Keith', 'shot61', False, 'Killer Keith'),
        ('Dave', 'hoop19', False, 'Dunkin’ Dave'),
        ('Sherry', 'pass48', False, 'Swish Sherry'),
        ('Tyler', 'hoop23', False, 'Triple-Double Tyler'),
        ('Nico', 'slam96', False, 'Net-Rippin’ Nico'),
        ('Sid', 'ball71', False, 'Sideline Sid')
    ]

    # Create users with specified passwords and fun names
    for username, password, is_admin, fun_name in users:
        user = User(username=username, is_admin=is_admin, fun_name=fun_name)
        user.set_password(password)
        db.session.add(user)
        print(f"User created: username={username}, password={password}, fun_name={fun_name}{' (Admin)' if is_admin else ''}")
    db.session.commit()

    # Create First Round (Round of 64) with simplified team names
    round1 = Round(name='First Round (Round of 64)', point_value=2, closed=False, closed_for_selection=False)
    db.session.add(round1)
    db.session.commit()

    games = [
        ("UConn", "Stetson"),
        ("Northwestern", "Florida Atlantic"),
        ("San Diego State", "UAB"),
        ("Yale", "Auburn"),
        ("Duquesne", "BYU"),
        ("Illinois", "Morehead State"),
        ("Washington State", "Drake"),
        ("Iowa State", "South Dakota State"),
        ("North Carolina", "Wagner"),
        ("Michigan State", "Mississippi State"),
        ("Grand Canyon", "Saint Mary's"),
        ("Alabama", "Charleston"),
        ("Clemson", "New Mexico"),
        ("Baylor", "Colgate"),
        ("Dayton", "Nevada"),
        ("Arizona", "Long Beach State"),
        ("Houston", "Longwood"),
        ("Texas A&M", "Nebraska"),
        ("James Madison", "Wisconsin"),
        ("Duke", "Vermont"),
        ("NC State", "Texas Tech"),
        ("Oakland", "Kentucky"),
        ("Colorado", "Florida"),
        ("Marquette", "Western Kentucky"),
        ("Purdue", "Grambling State"),
        ("Utah State", "TCU"),
        ("Gonzaga", "McNeese"),
        ("Kansas", "Samford"),
        ("Oregon", "South Carolina"),
        ("Creighton", "Akron"),
        ("Texas", "Colorado State"),
        ("Tennessee", "Saint Peter's")
    ]

    for team1, team2 in games:
        game = Game(round_id=round1.id, team1=team1, team2=team2)
        db.session.add(game)
    db.session.commit()

    print("Setup complete. Database reset and populated with:")
    print("First Round (Round of 64) initialized with 2024 NCAA Tournament teams.")