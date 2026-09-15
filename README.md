# March Madness 2025

A private family NCAA tournament bracket-picks app. Flask + SQLAlchemy + SQLite, deployed as a container alongside its sibling family apps. It has **no password store of its own** — see [Shared login with walktober](#shared-login-with-walktober) below before touching anything auth-related.

## Run locally

Requires Python 3.11+.

```sh
python -m venv venv
venv/bin/pip install -r requirements.txt   # Windows: venv\Scripts\pip
cp .env.example .env                        # fill in SECRET_KEY at minimum
venv/bin/python setup.py                    # creates tables, seeds users + Round 1 games
venv/bin/python app.py                      # http://localhost:5000
```

`setup.py` is destructive — `db.drop_all()` + `db.create_all()` on every run — so only run it against a database with no real picks in it yet. It seeds the roster from `USER_PROFILES` (no passwords: see below) and the current `GAMES` bracket for `TOURNAMENT_YEAR`.

Locally, `WALKTOBER_AUTH_URL` (in `.env.example`) defaults to the production Docker-network address and won't resolve outside that network — override it to a reachable walktober instance, or expect every local login to fail closed (see next section for why that's the correct failure mode).

## Shared login with walktober

This app stores no passwords. Login (`POST /login` in `app.py`) looks up the submitted username case-insensitively, then calls `verify_walktober_password()`, which POSTs `{username, password}` to **walktober's** Better Auth endpoint — `WALKTOBER_AUTH_URL` (`http://walktober:3000/api/auth/sign-in/username` inside the shared Docker network), with an `Origin: WALKTOBER_AUTH_ORIGIN` header (required by walktober's `trustedOrigins` check). HTTP 200 means authenticated; anything else — wrong password, network error, walktober down — fails closed.

Why: walktober already has a real account system (hashing, an organizer UI to create/edit/reset/remove accounts). A second local password store here would just be a second copy of every family member's password that could silently drift out of sync — which is exactly what happened before this existed. Now a password change in walktober takes effect here on the very next login, with nothing to keep in sync.

Consequences worth knowing:
- **Every username in `USER_PROFILES` (`setup.py`) must have an entry in `WALKTOBER_USERNAME_MAP` (`app.py`)**, mapping this app's username to the corresponding walktober username. `WalktoberAuthDelegationTests.test_every_march_madness_account_maps_to_a_walktober_username` (`tests/test_app.py`) enforces this — a seeded user with no mapping can never log in.
- march-madness usernames are capitalized ("Nate", "Don"); walktober's are lowercase ("nate", "don"). Login is deliberately case-insensitive on the local lookup so people don't need to remember two different casings for the same password.
- This app can't verify a login while walktober is unreachable. That's an accepted tradeoff for a private family app on one server — not a design to carry into anything with a higher availability bar.
- Changing walktober's Better Auth config (username plugin, `trustedOrigins`, or the sign-in response's success status) can silently break login here without touching this repo. See walktober's own README for the matching note.

## Deployment

Runs as a Docker container (`Dockerfile`, `compose.yaml`) on the same server as [walktober](https://github.com/n8tipple/walktober) and [whoswinningnow](https://github.com/n8tipple/whoswinningnow), fronted by walktober's Caddy instance — see walktober's README for the shared Caddy/network setup. `compose.yaml` joins the existing `walktober_family` Docker network as an external network and only `expose`s port 8000 (Caddy is the only internet-facing process).

To deploy or redeploy on the server:

```sh
git pull origin main
docker compose up -d --build
```

Run `setup.py` inside the container once (or again, if you accept wiping picks — see above) to seed/reseed:

```sh
docker compose exec app python setup.py
```

A `vercel.json` / `index.py` pair also exists for deploying to Vercel's Python runtime as an alternative target (`build_local_sqlite_url()` falls back to `/tmp` when `VERCEL=1`, since Vercel's filesystem is ephemeral). The container deployment above is what's actually running in production; Vercel is unused but kept working.

### Environment variables

See `.env.example` for the full list with defaults. The ones worth calling out:

- `SECRET_KEY` — Flask session signing key. Falls back to a local `secret_key.txt` file, then an ephemeral in-memory key with a startup warning if neither is set. Never commit a real one (see below).
- `DATABASE_URL` — omit to use local SQLite (`instance/mm<year>.db`, persisted via the `instance` Docker volume). Set to a `postgresql://...` URL to use Supabase/Postgres instead.
- `WALKTOBER_AUTH_URL` / `WALKTOBER_AUTH_ORIGIN` — see [Shared login with walktober](#shared-login-with-walktober).
- `HENRYGD_API_BASE_URL` / `HENRYGD_SPORT` / `HENRYGD_DIVISION` — source for `sync_tournament_from_henrygd()`, which pulls real bracket results to advance rounds and settle picks.

## History note

This repo's git history was rewritten once (force-pushed) to strip a committed `secret_key.txt` and a committed SQLite database containing real password hashes out of every commit — not just squashed away, since 100+ commits of real feature history were worth keeping. If you have an old local clone from before that rewrite, discard it and re-clone rather than merging; its history has diverged. The password store those old commits leaked no longer exists in this app at all (see above), so that data is now doubly moot, but the rewrite still happened for hygiene.

## Tests

```sh
python -m unittest tests.test_app -v
```

Covers bracket-advancement math, the HenryGD sync, pick/points flows, and the walktober-delegated login contract (mocked — no live walktober needed to run the suite).
