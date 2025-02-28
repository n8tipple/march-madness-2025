from app import app, db, User, Round, Game

with app.app_context():
    # Drop all tables to start fresh
    db.drop_all()
    # Recreate all tables
    db.create_all()

    # List of users with fixed passwords, admin status, corny fun names, and pictures
    users = [
        ('June', 'jump27', False, 'Jivin’ June', 'june.png'),      # Replaces "Slam Dunk June"
        ('Don', 'shot28', False, 'Dashin’ Don', 'don.png'),             # Replaces "Dribble Don"
        ('Nate', 'pass57', True, 'Nothin’ but Nate', 'nate.png'),      # Replaces "Nothin’ But Nate"
        ('Chris', 'pass18', True, 'Crossover Chris', 'chris.png'),        # Replaces "Crossover Chris"
        ('Casey', 'bball32', True, 'Center Court Casey', 'casey.png'),     # Replaces "Three-Point Casey"
        ('James', 'slam56', False, 'Jammin’ James', 'james.png'),      # Replaces "Jumpin’ James"
        ('Keith', 'shot61', False, 'Killer Keith', 'keith.png'),       # Replaces "Killer Keith"
        ('Dave', 'hoop19', False, 'Dunkin’ Dave', 'dave.png'),     # Replaces "Dunkin’ Dave"
        ('Sherry', 'swish48', False, 'Swishin’ Sherry', 'sherry.png'),   # Replaces "Swish Sherry"
        ('Tyler', 'hoop23', False, 'Three Point Tyler', 'tyler.png'),  # Replaces "Triple-Double Tyler"
        ('Meiko', 'slam96', False, 'Mighty Meiko', 'meiko.png'), # Replaces "Net-Rippin’ Meiko"
        ('Sid', 'ball71', False, 'Slammin’ Sid', 'sid.png')        # Replaces "Sideline Sid"
    ]

    # Create users with specified passwords, fun names, and pictures
    for username, password, is_admin, fun_name, picture in users:
        user = User(username=username, is_admin=is_admin, fun_name=fun_name, picture=picture)
        user.set_password(password)
        db.session.add(user)
        print(f"User created: username={username}, password={password}, fun_name={fun_name}, picture={picture}{' (Admin)' if is_admin else ''}")
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