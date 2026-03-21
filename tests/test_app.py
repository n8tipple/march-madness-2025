import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


TEST_DB_PATH = Path(tempfile.gettempdir()) / "march_madness_2026_test_suite.db"
os.environ["DATABASE_URL"] = f"sqlite:///{TEST_DB_PATH}"
os.environ["TOURNAMENT_YEAR"] = "2026"

from app import (  # noqa: E402
    Game,
    Pick,
    Round,
    User,
    app,
    build_henrygd_games_by_round,
    calculate_points,
    create_next_round,
    db,
    get_users_with_points,
    parse_non_negative_int,
    sync_round_matchups,
    sync_tournament_from_henrygd,
)


class BaseTestCase(unittest.TestCase):
    def setUp(self):
        app.config.update(TESTING=True)
        self.app_context = app.app_context()
        self.app_context.push()
        db.session.remove()
        db.drop_all()
        db.create_all()
        self.client = app.test_client()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.app_context.pop()

    def create_user(self, username, password="password123", is_admin=False, picture="nate.png"):
        user = User(
            username=username,
            is_admin=is_admin,
            fun_name=username.title(),
            picture=picture,
        )
        user.set_password(password)
        db.session.add(user)
        db.session.commit()
        return user

    def create_round(self, name, point_value=2, closed=False, closed_for_selection=False):
        round_obj = Round(
            name=name,
            point_value=point_value,
            closed=closed,
            closed_for_selection=closed_for_selection,
        )
        db.session.add(round_obj)
        db.session.commit()
        return round_obj

    def create_game(self, round_obj, team1, team2, winner=None):
        game = Game(round_id=round_obj.id, team1=team1, team2=team2, winner=winner)
        db.session.add(game)
        db.session.commit()
        return game

    def create_pick(self, user, game, picked_team, wager=0):
        pick = Pick(user_id=user.id, game_id=game.id, picked_team=picked_team, wager=wager)
        db.session.add(pick)
        db.session.commit()
        return pick

    def login(self, username, password="password123", follow_redirects=False):
        return self.client.post(
            "/login",
            data={"username": username, "password": password},
            follow_redirects=follow_redirects,
        )


class HelperTests(BaseTestCase):
    def test_parse_non_negative_int(self):
        self.assertEqual(parse_non_negative_int("42"), 42)
        self.assertEqual(parse_non_negative_int("-8"), 0)
        self.assertEqual(parse_non_negative_int("bad", default=5), 5)
        self.assertEqual(parse_non_negative_int(None), 0)

    def test_calculate_points_for_regular_round(self):
        round_obj = self.create_round("Sweet 16", point_value=4, closed=True, closed_for_selection=True)
        game = self.create_game(round_obj, "A", "B", winner="A")
        user_good = self.create_user("good")
        user_bad = self.create_user("bad")
        pick_good = self.create_pick(user_good, game, "A")
        pick_bad = self.create_pick(user_bad, game, "B")

        calculate_points(round_obj)

        self.assertEqual(db.session.get(Pick, pick_good.id).points, 4)
        self.assertEqual(db.session.get(Pick, pick_bad.id).points, 0)

    def test_calculate_points_for_championship_round(self):
        round_obj = self.create_round("Championship", point_value=16, closed=True, closed_for_selection=True)
        game = self.create_game(round_obj, "A", "B", winner="A")
        user_good = self.create_user("good")
        user_bad = self.create_user("bad")
        pick_good = self.create_pick(user_good, game, "A", wager=7)
        pick_bad = self.create_pick(user_bad, game, "B", wager=5)

        calculate_points(round_obj)

        self.assertEqual(db.session.get(Pick, pick_good.id).points, 7)
        self.assertEqual(db.session.get(Pick, pick_bad.id).points, -5)

    def test_create_next_round_builds_bracket_games(self):
        first_round = self.create_round("First Round (Round of 64)", point_value=2, closed=True, closed_for_selection=True)
        self.create_game(first_round, "A", "B", winner="A")
        self.create_game(first_round, "C", "D", winner="C")

        next_round = create_next_round(first_round)
        next_games = Game.query.filter_by(round_id=next_round.id).all()

        self.assertEqual(next_round.name, "Second Round (Round of 32)")
        self.assertEqual(next_round.point_value, 4)
        self.assertTrue(next_round.closed)
        self.assertTrue(next_round.closed_for_selection)
        self.assertEqual(len(next_games), 1)
        self.assertEqual(next_games[0].team1, "A")
        self.assertEqual(next_games[0].team2, "C")

    def test_create_next_round_uses_henrygd_matchup_order_when_available(self):
        first_round = self.create_round("First Round (Round of 64)", point_value=2, closed=True, closed_for_selection=True)
        self.create_game(first_round, "Ohio State", "TCU", winner="TCU")
        self.create_game(first_round, "Nebraska", "Troy", winner="Nebraska")
        self.create_game(first_round, "Louisville", "South Florida", winner="Louisville")
        self.create_game(first_round, "Wisconsin", "High Point", winner="High Point")
        self.create_game(first_round, "Duke", "Siena", winner="Duke")
        self.create_game(first_round, "Vanderbilt", "McNeese", winner="Vanderbilt")
        self.create_game(first_round, "Michigan State", "North Dakota State", winner="Michigan State")
        self.create_game(first_round, "Arkansas", "Hawaii", winner="Arkansas")

        external_games_by_round = {
            "Second Round (Round of 32)": [
                {"position_id": 301, "team1": "Duke", "team2": "TCU", "winner": None},
                {"position_id": 302, "team1": "Louisville", "team2": "Michigan St.", "winner": None},
                {"position_id": 303, "team1": "Nebraska", "team2": "Vanderbilt", "winner": None},
                {"position_id": 304, "team1": "High Point", "team2": "Arkansas", "winner": None},
            ]
        }

        with patch("app.build_henrygd_games_by_round", return_value=(external_games_by_round, {})):
            next_round = create_next_round(first_round, payload={"championships": [{}]})

        next_games = Game.query.filter_by(round_id=next_round.id).order_by(Game.id).all()
        self.assertEqual([(game.team1, game.team2) for game in next_games], [
            ("Duke", "TCU"),
            ("Louisville", "Michigan State"),
            ("Nebraska", "Vanderbilt"),
            ("High Point", "Arkansas"),
        ])

    def test_get_users_with_points_returns_sorted_users(self):
        round_obj = self.create_round("Elite Eight", point_value=8, closed=True, closed_for_selection=True)
        game = self.create_game(round_obj, "A", "B", winner="A")
        leader = self.create_user("leader")
        trailer = self.create_user("trailer")
        self.create_pick(leader, game, "A")
        self.create_pick(trailer, game, "B")

        calculate_points(round_obj)
        users = get_users_with_points()

        self.assertGreaterEqual(len(users), 2)
        self.assertEqual(users[0].username, "leader")
        self.assertEqual(users[1].username, "trailer")
        self.assertEqual(users[0].points, 8)
        self.assertEqual(users[1].points, 0)

    def test_build_henrygd_games_by_round_maps_round_depths_and_skips_first_four(self):
        payload = {
            "championships": [
                {
                    "games": [
                        {"bracketPositionId": 0, "victorBracketPositionId": 1, "startTimeEpoch": 100, "teams": [{"nameShort": "P", "isWinner": True}, {"nameShort": "Q", "isWinner": False}]},
                        {"bracketPositionId": 1, "victorBracketPositionId": 2, "startTimeEpoch": 200, "teams": [{"nameShort": "A", "isWinner": True}, {"nameShort": "B", "isWinner": False}]},
                        {"bracketPositionId": 2, "victorBracketPositionId": 3, "startTimeEpoch": 300, "teams": [{"nameShort": "A", "isWinner": True}, {"nameShort": "C", "isWinner": False}]},
                        {"bracketPositionId": 3, "victorBracketPositionId": 4, "startTimeEpoch": 400, "teams": [{"nameShort": "A", "isWinner": True}, {"nameShort": "D", "isWinner": False}]},
                        {"bracketPositionId": 4, "victorBracketPositionId": 5, "startTimeEpoch": 500, "teams": [{"nameShort": "A", "isWinner": True}, {"nameShort": "E", "isWinner": False}]},
                        {"bracketPositionId": 5, "victorBracketPositionId": 6, "startTimeEpoch": 600, "teams": [{"nameShort": "A", "isWinner": True}, {"nameShort": "F", "isWinner": False}]},
                        {"bracketPositionId": 6, "victorBracketPositionId": None, "startTimeEpoch": 700, "teams": [{"nameShort": "A", "isWinner": True}, {"nameShort": "G", "isWinner": False}]},
                    ]
                }
            ]
        }

        games_by_round, team_info = build_henrygd_games_by_round(payload)

        self.assertIn("First Round (Round of 64)", games_by_round)
        self.assertIn("Second Round (Round of 32)", games_by_round)
        self.assertIn("Championship", games_by_round)
        self.assertEqual(len(games_by_round["First Round (Round of 64)"]), 1)
        self.assertEqual(games_by_round["First Round (Round of 64)"][0]["team1"], "A")
        self.assertEqual(games_by_round["First Round (Round of 64)"][0]["winner"], "A")
        self.assertEqual(team_info["a"]["name"], "A")

    def test_build_henrygd_games_by_round_sorts_rounds_by_tipoff_time(self):
        payload = {
            "championships": [
                {
                    "games": [
                        {"bracketPositionId": 101, "victorBracketPositionId": 201, "startTimeEpoch": 300, "teams": [{"nameShort": "Late", "isWinner": False}, {"nameShort": "Later", "isWinner": False}]},
                        {"bracketPositionId": 102, "victorBracketPositionId": 201, "startTimeEpoch": 100, "teams": [{"nameShort": "Early", "isWinner": False}, {"nameShort": "Soon", "isWinner": False}]},
                        {"bracketPositionId": 201, "victorBracketPositionId": 301, "startTimeEpoch": 400, "teams": [{"nameShort": "Mid A", "isWinner": False}, {"nameShort": "Mid B", "isWinner": False}]},
                        {"bracketPositionId": 301, "victorBracketPositionId": 401, "startTimeEpoch": 500, "teams": [{"nameShort": "QF A", "isWinner": False}, {"nameShort": "QF B", "isWinner": False}]},
                        {"bracketPositionId": 401, "victorBracketPositionId": 501, "startTimeEpoch": 600, "teams": [{"nameShort": "SF A", "isWinner": False}, {"nameShort": "SF B", "isWinner": False}]},
                        {"bracketPositionId": 501, "victorBracketPositionId": 601, "startTimeEpoch": 700, "teams": [{"nameShort": "FF A", "isWinner": False}, {"nameShort": "FF B", "isWinner": False}]},
                        {"bracketPositionId": 601, "victorBracketPositionId": None, "startTimeEpoch": 800, "teams": [{"nameShort": "Final A", "isWinner": False}, {"nameShort": "Final B", "isWinner": False}]},
                    ]
                }
            ]
        }

        games_by_round, _ = build_henrygd_games_by_round(payload)

        self.assertEqual(
            [(game["team1"], game["start_time_epoch"]) for game in games_by_round["First Round (Round of 64)"]],
            [("Early", 100), ("Late", 300)],
        )

    def test_sync_round_matchups_updates_existing_round_games(self):
        first_round = self.create_round("First Round (Round of 64)", closed=True, closed_for_selection=True)
        self.create_game(first_round, "A", "B", winner="A")
        self.create_game(first_round, "C", "D", winner="C")
        self.create_game(first_round, "E", "F", winner="E")
        self.create_game(first_round, "G", "H", winner="G")

        second_round = self.create_round("Second Round (Round of 32)", closed=True, closed_for_selection=True)
        game1 = self.create_game(second_round, "Old 1", "Old 2", winner="Old 1")
        game2 = self.create_game(second_round, "Old 3", "Old 4")

        summary = sync_round_matchups(second_round)
        db.session.commit()

        refreshed_game1 = db.session.get(Game, game1.id)
        refreshed_game2 = db.session.get(Game, game2.id)
        self.assertEqual(summary["games_updated"], 2)
        self.assertEqual(summary["games_skipped"], 0)
        self.assertEqual(summary["winners_cleared"], 1)
        self.assertEqual(refreshed_game1.team1, "A")
        self.assertEqual(refreshed_game1.team2, "C")
        self.assertIsNone(refreshed_game1.winner)
        self.assertEqual(refreshed_game2.team1, "E")
        self.assertEqual(refreshed_game2.team2, "G")

    def test_calculate_points_resets_points_when_winner_cleared(self):
        round_obj = self.create_round("Sweet 16", point_value=4, closed=True, closed_for_selection=True)
        game = self.create_game(round_obj, "A", "B", winner="A")
        user = self.create_user("nate")
        pick = self.create_pick(user, game, "A")

        calculate_points(round_obj)
        self.assertEqual(db.session.get(Pick, pick.id).points, 4)

        game.winner = None
        db.session.commit()

        calculate_points(round_obj)
        self.assertEqual(db.session.get(Pick, pick.id).points, 0)

    def test_sync_tournament_from_henrygd_updates_winners_and_creates_second_round(self):
        user = self.create_user("nate")
        round_obj = self.create_round("First Round (Round of 64)", point_value=2, closed=False, closed_for_selection=False)
        game1 = self.create_game(round_obj, "A", "B")
        game2 = self.create_game(round_obj, "C", "D")
        game3 = self.create_game(round_obj, "E", "F")
        game4 = self.create_game(round_obj, "G", "H")
        self.create_pick(user, game1, "A")
        self.create_pick(user, game2, "D")

        payload = {
            "championships": [
                {
                    "games": [
                        {"bracketPositionId": 101, "victorBracketPositionId": 201, "teams": [{"nameShort": "A", "isWinner": True}, {"nameShort": "B", "isWinner": False}]},
                        {"bracketPositionId": 102, "victorBracketPositionId": 201, "teams": [{"nameShort": "C", "isWinner": True}, {"nameShort": "D", "isWinner": False}]},
                        {"bracketPositionId": 103, "victorBracketPositionId": 202, "teams": [{"nameShort": "E", "isWinner": True}, {"nameShort": "F", "isWinner": False}]},
                        {"bracketPositionId": 104, "victorBracketPositionId": 202, "teams": [{"nameShort": "G", "isWinner": True}, {"nameShort": "H", "isWinner": False}]},
                        {"bracketPositionId": 201, "victorBracketPositionId": 301, "teams": [{"nameShort": "A", "isWinner": False}, {"nameShort": "C", "isWinner": False}]},
                        {"bracketPositionId": 202, "victorBracketPositionId": 301, "teams": [{"nameShort": "E", "isWinner": False}, {"nameShort": "G", "isWinner": False}]},
                        {"bracketPositionId": 301, "victorBracketPositionId": 401, "teams": [{"nameShort": "A", "isWinner": False}, {"nameShort": "E", "isWinner": False}]},
                        {"bracketPositionId": 401, "victorBracketPositionId": 501, "teams": [{"nameShort": "A", "isWinner": False}, {"nameShort": "I", "isWinner": False}]},
                        {"bracketPositionId": 501, "victorBracketPositionId": 601, "teams": [{"nameShort": "A", "isWinner": False}, {"nameShort": "J", "isWinner": False}]},
                        {"bracketPositionId": 601, "victorBracketPositionId": None, "teams": [{"nameShort": "A", "isWinner": False}, {"nameShort": "K", "isWinner": False}]},
                    ]
                }
            ]
        }

        summary = sync_tournament_from_henrygd(payload=payload)

        refreshed_round = db.session.get(Round, round_obj.id)
        self.assertEqual(summary["winners_updated"], 4)
        self.assertIn("First Round (Round of 64)", summary["rounds_closed"])
        self.assertTrue(refreshed_round.closed)
        self.assertTrue(refreshed_round.closed_for_selection)

        second_round = Round.query.filter_by(name="Second Round (Round of 32)").first()
        self.assertIsNotNone(second_round)
        second_round_games = Game.query.filter_by(round_id=second_round.id).order_by(Game.id).all()
        self.assertEqual(len(second_round_games), 2)
        self.assertEqual(second_round_games[0].team1, "A")
        self.assertEqual(second_round_games[0].team2, "C")
        self.assertEqual(second_round_games[1].team1, "E")
        self.assertEqual(second_round_games[1].team2, "G")

        scored_pick = Pick.query.filter_by(user_id=user.id, game_id=game1.id).first()
        missed_pick = Pick.query.filter_by(user_id=user.id, game_id=game2.id).first()
        self.assertEqual(scored_pick.points, 2)
        self.assertEqual(missed_pick.points, 0)


class AuthAndHomeRouteTests(BaseTestCase):
    def test_login_page_loads(self):
        response = self.client.get("/login")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Login", response.data)

    def test_invalid_login_shows_message(self):
        self.create_user("nate")
        response = self.login("nate", password="wrong", follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Invalid username or password", response.data)

    def test_valid_login_redirects_to_dashboard(self):
        self.create_user("nate")
        response = self.login("nate")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/dashboard", response.headers["Location"])

    def test_logout_redirects_to_home(self):
        self.create_user("nate")
        self.login("nate")
        response = self.client.get("/logout")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/", response.headers["Location"])

    def test_home_with_no_rounds_shows_empty_state(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"No active round right now", response.data)

    def test_home_redirects_authenticated_user_to_dashboard_when_active(self):
        self.create_user("nate")
        round_obj = self.create_round("First Round (Round of 64)", closed=False, closed_for_selection=False)
        self.create_game(round_obj, "A", "B")
        self.login("nate")

        response = self.client.get("/")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/dashboard", response.headers["Location"])

    def test_home_shows_landing_page_when_everything_complete(self):
        round_obj = self.create_round("Championship", closed=True, closed_for_selection=True)
        self.create_game(round_obj, "A", "B", winner="A")

        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"No active round right now", response.data)
        self.assertIn(b"Warmup Mode", response.data)


class PickRouteTests(BaseTestCase):
    def test_pick_requires_login(self):
        response = self.client.get("/pick")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login", response.headers["Location"])

    def test_pick_redirects_when_no_open_rounds(self):
        self.create_user("nate")
        self.login("nate")
        response = self.client.get("/pick", follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"No open rounds available for picks", response.data)

    def test_pick_page_loads_with_open_round_games(self):
        user = self.create_user("nate")
        round_obj = self.create_round("First Round (Round of 64)", closed=False, closed_for_selection=False)
        self.create_game(round_obj, "UConn", "Purdue")
        self.login(user.username)

        response = self.client.get("/pick")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"UConn", response.data)
        self.assertIn(b"Purdue", response.data)

    def test_pick_post_missing_game_selection_does_not_commit_partial_data(self):
        user = self.create_user("nate")
        round_obj = self.create_round("First Round (Round of 64)", closed=False, closed_for_selection=False)
        game1 = self.create_game(round_obj, "A", "B")
        self.create_game(round_obj, "C", "D")
        self.login(user.username)

        response = self.client.post("/pick", data={f"game{game1.id}": "A"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Pick.query.filter_by(user_id=user.id).count(), 0)
        self.assertIn(b"Please select a team for this game", response.data)

    def test_pick_post_creates_all_picks(self):
        user = self.create_user("nate")
        round_obj = self.create_round("First Round (Round of 64)", closed=False, closed_for_selection=False)
        game1 = self.create_game(round_obj, "A", "B")
        game2 = self.create_game(round_obj, "C", "D")
        self.login(user.username)

        response = self.client.post(
            "/pick",
            data={f"game{game1.id}": "A", f"game{game2.id}": "D"},
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn("/", response.headers["Location"])
        self.assertEqual(Pick.query.filter_by(user_id=user.id).count(), 2)

    def test_pick_championship_wager_is_clamped_to_available_points(self):
        user = self.create_user("nate")
        closed_round = self.create_round("Elite Eight", point_value=10, closed=True, closed_for_selection=True)
        scored_game = self.create_game(closed_round, "A", "B", winner="A")
        self.create_pick(user, scored_game, "A")
        calculate_points(closed_round)

        championship = self.create_round("Championship", point_value=16, closed=False, closed_for_selection=False)
        final_game = self.create_game(championship, "X", "Y")

        self.login(user.username)
        response = self.client.post(
            "/pick",
            data={f"game{final_game.id}": "X", "wager": "999"},
        )
        self.assertEqual(response.status_code, 302)

        saved_pick = Pick.query.filter_by(user_id=user.id, game_id=final_game.id).first()
        self.assertIsNotNone(saved_pick)
        self.assertEqual(saved_pick.wager, 10)


class AdminRouteTests(BaseTestCase):
    def test_admin_requires_admin_user(self):
        user = self.create_user("nate", is_admin=False)
        self.login(user.username)
        response = self.client.get("/admin", follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Access denied", response.data)

    def test_admin_page_loads_for_admin(self):
        admin = self.create_user("admin", is_admin=True)
        self.login(admin.username)
        response = self.client.get("/admin")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Admin Panel", response.data)

    def test_admin_post_updates_round_and_winner(self):
        admin = self.create_user("admin", is_admin=True)
        round_obj = self.create_round("First Round (Round of 64)", closed=False, closed_for_selection=False)
        game = self.create_game(round_obj, "A", "B")
        self.login(admin.username)

        response = self.client.post(
            "/admin",
            data={
                "round_id": round_obj.id,
                "closed": "on",
                "point_value": "3",
                f"game{game.id}_winner": "A",
            },
        )
        self.assertEqual(response.status_code, 302)

        refreshed_round = db.session.get(Round, round_obj.id)
        refreshed_game = db.session.get(Game, game.id)
        self.assertTrue(refreshed_round.closed)
        self.assertTrue(refreshed_round.closed_for_selection)
        self.assertEqual(refreshed_round.point_value, 3)
        self.assertEqual(refreshed_game.winner, "A")

    def test_admin_next_round_creation_creates_second_round(self):
        admin = self.create_user("admin", is_admin=True)
        first_round = self.create_round("First Round (Round of 64)", closed=False, closed_for_selection=False)
        game1 = self.create_game(first_round, "A", "B", winner="A")
        game2 = self.create_game(first_round, "C", "D", winner="C")
        self.login(admin.username)

        response = self.client.post(
            "/admin",
            data={
                "round_id": first_round.id,
                "point_value": "2",
                f"game{game1.id}_winner": "A",
                f"game{game2.id}_winner": "C",
                "next_round": "true",
            },
        )
        self.assertEqual(response.status_code, 302)

        second_round = Round.query.filter_by(name="Second Round (Round of 32)").first()
        self.assertIsNotNone(second_round)
        created_games = Game.query.filter_by(round_id=second_round.id).all()
        self.assertEqual(len(created_games), 1)
        self.assertEqual(created_games[0].team1, "A")
        self.assertEqual(created_games[0].team2, "C")

    def test_admin_submit_picks_requires_admin(self):
        user = self.create_user("player", is_admin=False)
        self.login(user.username)
        response = self.client.get("/admin_submit_picks", follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Access denied", response.data)

    def test_admin_submit_picks_page_loads_for_admin(self):
        admin = self.create_user("admin", is_admin=True)
        self.create_round("First Round (Round of 64)", closed=False, closed_for_selection=False)
        self.login(admin.username)
        response = self.client.get("/admin_submit_picks")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Submit Picks for Users", response.data)

    def test_admin_submit_picks_creates_pick_for_selected_user(self):
        admin = self.create_user("admin", is_admin=True)
        player = self.create_user("player", is_admin=False)
        round_obj = self.create_round("First Round (Round of 64)", closed=False, closed_for_selection=False)
        game = self.create_game(round_obj, "A", "B")
        self.login(admin.username)

        response = self.client.post(
            "/admin_submit_picks",
            data={
                "round_id": round_obj.id,
                "user_id": player.id,
                f"game{game.id}": "A",
                "submit_picks": "true",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn("/admin", response.headers["Location"])

        saved_pick = Pick.query.filter_by(user_id=player.id, game_id=game.id).first()
        self.assertIsNotNone(saved_pick)
        self.assertEqual(saved_pick.picked_team, "A")

    def test_admin_submit_picks_invalid_round_id_falls_back_to_first_open_round(self):
        admin = self.create_user("admin", is_admin=True)
        first_open = self.create_round("First Round (Round of 64)", closed=False, closed_for_selection=False)
        self.create_round("Second Round (Round of 32)", closed=False, closed_for_selection=False)
        self.login(admin.username)

        response = self.client.get("/admin_submit_picks?round_id=999")
        self.assertEqual(response.status_code, 200)
        self.assertIn(first_open.name.encode("utf-8"), response.data)

    def test_admin_sync_henrygd_requires_admin(self):
        user = self.create_user("nate", is_admin=False)
        self.login(user.username)
        response = self.client.post("/admin_sync_henrygd", follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Access denied", response.data)

    def test_admin_sync_henrygd_runs_sync_and_shows_summary(self):
        admin = self.create_user("admin", is_admin=True)
        round_obj = self.create_round("First Round (Round of 64)", closed=False, closed_for_selection=False)
        self.login(admin.username)

        with patch(
            "app.sync_tournament_from_henrygd",
            return_value={"winners_updated": 3, "rounds_closed": ["First Round (Round of 64)"], "rounds_created": ["Second Round (Round of 32)"]},
        ):
            response = self.client.post("/admin_sync_henrygd", data={"round_id": round_obj.id}, follow_redirects=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Winner sync complete: 3 winner(s) updated, 1 round(s) closed, 1 round(s) created.", response.data)

    def test_admin_sync_matchups_updates_selected_round(self):
        admin = self.create_user("admin", is_admin=True)
        first_round = self.create_round("First Round (Round of 64)", closed=True, closed_for_selection=True)
        self.create_game(first_round, "Ohio State", "TCU", winner="TCU")
        self.create_game(first_round, "Nebraska", "Troy", winner="Nebraska")
        self.create_game(first_round, "Louisville", "South Florida", winner="Louisville")
        self.create_game(first_round, "Wisconsin", "High Point", winner="High Point")
        self.create_game(first_round, "Duke", "Siena", winner="Duke")
        self.create_game(first_round, "Vanderbilt", "McNeese", winner="Vanderbilt")
        self.create_game(first_round, "Michigan State", "North Dakota State", winner="Michigan State")
        self.create_game(first_round, "Arkansas", "Hawaii", winner="Arkansas")
        second_round = self.create_round("Second Round (Round of 32)", closed=False, closed_for_selection=False)
        games = [
            self.create_game(second_round, "Placeholder 1", "Placeholder 2", winner="Placeholder 1"),
            self.create_game(second_round, "Placeholder 3", "Placeholder 4"),
            self.create_game(second_round, "Placeholder 5", "Placeholder 6"),
            self.create_game(second_round, "Placeholder 7", "Placeholder 8"),
        ]
        external_games_by_round = {
            "Second Round (Round of 32)": [
                {"position_id": 301, "team1": "Duke", "team2": "TCU", "winner": None},
                {"position_id": 302, "team1": "Louisville", "team2": "Michigan St.", "winner": None},
                {"position_id": 303, "team1": "Nebraska", "team2": "Vanderbilt", "winner": None},
                {"position_id": 304, "team1": "High Point", "team2": "Arkansas", "winner": None},
            ]
        }
        self.login(admin.username)

        with patch("app.fetch_henrygd_bracket_payload", return_value={"championships": [{}]}), patch(
            "app.build_henrygd_games_by_round",
            return_value=(external_games_by_round, {}),
        ):
            response = self.client.post("/admin_sync_matchups", data={"round_id": second_round.id}, follow_redirects=True)

        self.assertEqual(response.status_code, 200)
        refreshed_games = [db.session.get(Game, game.id) for game in games]
        self.assertEqual([(game.team1, game.team2) for game in refreshed_games], [
            ("Duke", "TCU"),
            ("Louisville", "Michigan State"),
            ("Nebraska", "Vanderbilt"),
            ("High Point", "Arkansas"),
        ])
        self.assertIsNone(refreshed_games[0].winner)
        self.assertIn(
            b"Matchup sync complete using HenryGD bracket data: 4 game(s) updated, 0 game(s) skipped, 1 winner(s) cleared.",
            response.data,
        )


class ViewAndLeaderboardRouteTests(BaseTestCase):
    def test_leaderboard_page_loads(self):
        user = self.create_user("nate")
        response = self.client.get("/leaderboard")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Leaderboard", response.data)
        self.assertIn(user.username.encode("utf-8"), response.data)

    def test_leaderboard_modal_shows_round_pick_breakdown(self):
        user = self.create_user("nate")
        closed_round = self.create_round("Sweet 16", point_value=4, closed=True, closed_for_selection=True)
        game = self.create_game(closed_round, "A", "B", winner="A")
        self.create_pick(user, game, "A")
        calculate_points(closed_round)

        response = self.client.get("/leaderboard")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Sweet 16", response.data)
        self.assertIn(b"<strong>A</strong>", response.data)
        self.assertIn(b'<span class="vs-text">vs</span>', response.data)
        self.assertIn(b'<span class="team-with-logo">B</span>', response.data)
        self.assertNotIn(b"leader-inline-pick", response.data)
        self.assertIn(b"Round Total", response.data)
        self.assertNotIn(b">Result<", response.data)

    def test_view_picks_requires_login(self):
        response = self.client.get("/view_picks")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login", response.headers["Location"])

    def test_view_picks_without_closed_rounds_redirects(self):
        user = self.create_user("nate")
        self.login(user.username)
        response = self.client.get("/view_picks", follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"No closed rounds available to view picks", response.data)

    def test_view_picks_with_closed_rounds_displays_table(self):
        user = self.create_user("nate")
        other = self.create_user("other")
        closed_round = self.create_round("Sweet 16", point_value=4, closed=True, closed_for_selection=True)
        game = self.create_game(closed_round, "A", "B", winner="A")
        self.create_pick(user, game, "A")
        self.create_pick(other, game, "B")
        calculate_points(closed_round)

        self.login(user.username)
        response = self.client.get("/view_picks")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"View Picks", response.data)
        self.assertIn(b"Round Total", response.data)
        self.assertIn(user.username.encode("utf-8"), response.data)

    def test_static_images_send_cache_headers(self):
        response = self.client.get("/static/nate.png")
        try:
            self.assertEqual(response.status_code, 200)
            cache_control = response.headers.get("Cache-Control", "")
            self.assertIn("public", cache_control)
            self.assertIn("max-age=2592000", cache_control)
        finally:
            response.close()


if __name__ == "__main__":
    unittest.main()
