import json
import os
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request

from app import app, db, User, Round, Game

try:
    import certifi
except ImportError:
    certifi = None

TOURNAMENT_YEAR = 2026

USERS = [
    ('donjune', 'jump27', False, 'The Don of a New June', 'don_june.png'),
    ('Nate', 'pass57', True, 'Net Rippin’ Nate', 'nate.png'),
    ('Chris', 'pass18', True, 'Clutch Chris', 'chris.png'),
    ('Casey', 'bball32', True, 'Coast-to-Coast Casey', 'casey.png'),
    ('James', 'slam56', False, 'Fast Break James', 'james.png'),
    ('Keith', 'shot61', False, 'Key Shot Keith', 'keith.png'),
    ('Dave', 'hoop19', False, 'Drive Lane Dave', 'dave.png'),
    ('Sherry', 'swish48', False, 'Sharp Shooter Sherry', 'sherry.png'),
    ('Tyler', 'hoop23', False, 'Triple Threat Tyler', 'tyler.png'),
    ('Meiko', 'slam96', False, 'Money Meiko', 'meiko.png'),
]

GAMES = [
    # Thursday, March 19
    ("Ohio State", "TCU"),
    ("Nebraska", "Troy"),
    ("Louisville", "South Florida"),
    ("Wisconsin", "High Point"),
    ("Duke", "Siena"),
    ("Vanderbilt", "McNeese"),
    ("Michigan State", "North Dakota State"),
    ("Arkansas", "Hawaii"),
    ("North Carolina", "VCU"),
    ("Michigan", "Howard"),
    ("BYU", "Texas"),
    ("Saint Mary's", "Texas A&M"),
    ("Illinois", "Penn"),
    ("Georgia", "Saint Louis"),
    ("Gonzaga", "Kennesaw State"),
    ("Houston", "Idaho"),
    # Friday, March 20
    ("Kentucky", "Santa Clara"),
    ("Texas Tech", "Akron"),
    ("Arizona", "Long Island"),
    ("Virginia", "Wright State"),
    ("Iowa State", "Tennessee State"),
    ("Alabama", "Hofstra"),
    ("Villanova", "Utah State"),
    ("Tennessee", "Miami (Ohio)"),
    ("Clemson", "Iowa"),
    ("St. John's", "Northern Iowa"),
    ("UCLA", "UCF"),
    ("Purdue", "Queens"),
    ("Florida", "Prairie View A&M"),
    ("Kansas", "Cal Baptist"),
    ("UConn", "Furman"),
    ("Miami (Fla.)", "Missouri"),
]


class SupabaseRestClient:
    def __init__(self, project_url, api_key):
        self.base_url = project_url.rstrip('/')
        self.api_key = api_key
        self.ssl_context = self._build_ssl_context()

    @staticmethod
    def _build_ssl_context():
        custom_ca_bundle = os.getenv('SUPABASE_CA_BUNDLE') or os.getenv('SSL_CERT_FILE')
        if custom_ca_bundle:
            return ssl.create_default_context(cafile=custom_ca_bundle)
        if certifi is not None:
            return ssl.create_default_context(cafile=certifi.where())
        return ssl.create_default_context()

    def request(self, method, table, payload=None, query=None, prefer='return=representation'):
        endpoint = f"{self.base_url}/rest/v1/{table}"
        if query:
            endpoint = f"{endpoint}?{urllib.parse.urlencode(query, doseq=True)}"

        headers = {
            'apikey': self.api_key,
            'Authorization': f'Bearer {self.api_key}',
            'Accept': 'application/json',
        }
        if prefer:
            headers['Prefer'] = prefer

        body = None
        if payload is not None:
            headers['Content-Type'] = 'application/json'
            body = json.dumps(payload).encode('utf-8')

        req = urllib.request.Request(endpoint, data=body, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=30, context=self.ssl_context) as response:
                data = response.read().decode('utf-8')
                return json.loads(data) if data else None
        except urllib.error.HTTPError as exc:
            error_body = exc.read().decode('utf-8')
            raise RuntimeError(f"Supabase API error {exc.code} for {table}: {error_body}") from exc
        except urllib.error.URLError as exc:
            reason = str(exc.reason)
            if 'CERTIFICATE_VERIFY_FAILED' in reason:
                raise RuntimeError(
                    "Supabase TLS verification failed. Install certifi "
                    "(`venv/bin/python -m pip install certifi`) or set SUPABASE_CA_BUNDLE/SSL_CERT_FILE."
                ) from exc
            raise RuntimeError(f"Supabase API network error for {table}: {exc.reason}") from exc

    def delete_all_rows(self, table):
        # Use a broad id filter because PostgREST requires at least one filter for DELETE.
        self.request('DELETE', table, query={'id': 'gte.0'}, prefer='return=minimal')


def setup_with_sqlalchemy():
    with app.app_context():
        db.drop_all()
        db.create_all()

        for username, password, is_admin, fun_name, picture in USERS:
            user = User(username=username, is_admin=is_admin, fun_name=fun_name, picture=picture)
            user.set_password(password)
            db.session.add(user)
            print(f"User created: username={username}, password={password}, fun_name={fun_name}, picture={picture}{' (Admin)' if is_admin else ''}")
        db.session.commit()

        round1 = Round(name='First Round (Round of 64)', point_value=2, closed=False, closed_for_selection=False)
        db.session.add(round1)
        db.session.commit()

        for team1, team2 in GAMES:
            db.session.add(Game(round_id=round1.id, team1=team1, team2=team2))
        db.session.commit()

        print("Setup complete using SQLAlchemy.")
        print(f"First Round (Round of 64) initialized for {TOURNAMENT_YEAR}, no picks created.")


def setup_with_supabase_api():
    project_url = os.getenv('SUPABASE_URL') or os.getenv('NEXT_PUBLIC_SUPABASE_URL')
    service_key = os.getenv('SUPABASE_SERVICE_ROLE_KEY') or os.getenv('SUPABASE_SECRET_KEY')

    if not project_url or not service_key:
        raise RuntimeError(
            "Supabase API setup requires SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY "
            "(or SUPABASE_SECRET_KEY)."
        )
    if service_key.startswith('sb_publishable_'):
        raise RuntimeError(
            "Publishable keys cannot seed/reset the database. "
            "Use SUPABASE_SERVICE_ROLE_KEY (legacy service_role JWT) or an sb_secret_* key."
        )

    client = SupabaseRestClient(project_url, service_key)

    # Clear dependent tables first to satisfy foreign keys.
    for table in ('pick', 'game', 'round', 'user'):
        client.delete_all_rows(table)

    user_rows = []
    for username, password, is_admin, fun_name, picture in USERS:
        user = User(username=username, is_admin=is_admin, fun_name=fun_name, picture=picture)
        user.set_password(password)
        user_rows.append(
            {
                'username': username,
                'password_hash': user.password_hash,
                'points': 0,
                'is_admin': is_admin,
                'fun_name': fun_name,
                'picture': picture,
            }
        )
        print(f"Prepared user for Supabase: username={username}, password={password}{' (Admin)' if is_admin else ''}")

    inserted_users = client.request('POST', 'user', payload=user_rows)
    if not inserted_users or len(inserted_users) != len(USERS):
        raise RuntimeError("Failed to insert all users via Supabase API.")

    inserted_round = client.request(
        'POST',
        'round',
        payload=[{
            'name': 'First Round (Round of 64)',
            'point_value': 2,
            'closed': False,
            'closed_for_selection': False,
        }],
    )
    if not inserted_round:
        raise RuntimeError("Failed to create first round via Supabase API.")
    round_id = inserted_round[0]['id']

    game_rows = [{'round_id': round_id, 'team1': team1, 'team2': team2, 'winner': None} for team1, team2 in GAMES]
    inserted_games = client.request('POST', 'game', payload=game_rows, prefer='return=minimal')
    if inserted_games not in (None, []):
        # keep behavior explicit if Supabase returns representations in the future
        print(f"Supabase returned {len(inserted_games)} game rows.")

    print("Setup complete using Supabase REST API.")
    print(f"First Round (Round of 64) initialized for {TOURNAMENT_YEAR}, no picks created.")


if __name__ == '__main__':
    use_supabase_api = (
        os.getenv('SUPABASE_URL') and (
            os.getenv('SUPABASE_SERVICE_ROLE_KEY') or os.getenv('SUPABASE_SECRET_KEY')
        ) and not os.getenv('DATABASE_URL')
    )

    try:
        if use_supabase_api:
            print("Running setup using Supabase REST API env vars.")
            setup_with_supabase_api()
        else:
            print("Running setup using SQLAlchemy DATABASE_URL/local SQLite.")
            setup_with_sqlalchemy()
    except Exception as exc:
        print(f"Setup failed: {exc}", file=sys.stderr)
        sys.exit(1)
