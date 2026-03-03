from app import app, db, User, Round, Game, Pick

TOURNAMENT_YEAR = 2026

with app.app_context():
    # Drop all tables to start fresh
    db.drop_all()
    # Recreate all tables
    db.create_all()

    # List of users with fixed passwords, admin status, corny fun names, and pictures
    users = [
        ('June', 'jump27', False, 'Jump Shot June', 'june.png'),
        ('Don', 'shot28', False, 'Downtown Don', 'don.png'),
        ('Nate', 'pass57', True, 'Net Rippin’ Nate', 'nate.png'),
        ('Chris', 'pass18', True, 'Clutch Chris', 'chris.png'),
        ('Casey', 'bball32', True, 'Coast-to-Coast Casey', 'casey.png'),
        ('James', 'slam56', False, 'Fast Break James', 'james.png'),
        ('Keith', 'shot61', False, 'Key Shot Keith', 'keith.png'),
        ('Dave', 'hoop19', False, 'Drive Lane Dave', 'dave.png'),
        ('Sherry', 'swish48', False, 'Sharp Shooter Sherry', 'sherry.png'),
        ('Tyler', 'hoop23', False, 'Triple Threat Tyler', 'tyler.png'),
        ('Meiko', 'slam96', False, 'Money Meiko', 'meiko.png'),
        ('Sid', 'ball71', False, 'Sixth Man Sid', 'sid.png')
    ]

    # Create users with specified passwords, fun names, and pictures
    user_objects = {}
    for username, password, is_admin, fun_name, picture in users:
        user = User(username=username, is_admin=is_admin, fun_name=fun_name, picture=picture)
        user.set_password(password)
        db.session.add(user)
        user_objects[username] = user
        print(f"User created: username={username}, password={password}, fun_name={fun_name}, picture={picture}{' (Admin)' if is_admin else ''}")
    db.session.commit()

    # Create First Round (Round of 64) with simplified team names
    round1 = Round(name='First Round (Round of 64)', point_value=2, closed=False, closed_for_selection=False)
    db.session.add(round1)
    db.session.commit()

    # First-round matchups (update this list when the official bracket is final)
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

    game_objects = []
    for team1, team2 in games:
        game = Game(round_id=round1.id, team1=team1, team2=team2)
        db.session.add(game)
        game_objects.append(game)
    db.session.commit()

    print("Setup complete. Database reset and populated with:")
    print(f"First Round (Round of 64) initialized for {TOURNAMENT_YEAR}, no picks created.")
