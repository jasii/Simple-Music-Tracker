# Simple Music Tracker

A barebones, text-first web app that scans your local music library, lists every
artist, and tracks upcoming album releases. Subscribe to artists with a single
click (YouTube-style: **Subscribe** shows them on the site, **Subscribe + Notify**
also fires a webhook when a new release appears).

No frameworks on the frontend, no build step, minimal dependencies. SQLite for
storage. Dark (AMOLED pure-black) and light themes with a toggle. Installable as
a PWA with a mobile-friendly UI.

## Features

- **Library scan** reads artist tags from your music files (mp3, flac, m4a, ogg,
  opus, wav, and more) and builds a de-duplicated artist list with track counts.
- **Fast subscribe/unsubscribe** via a filterable table of checkboxes — built for
  thousands of artists. Bulk-select rows to subscribe or unsubscribe in one go.
- **Two subscription levels** per artist:
  - *Subscribe* — the artist shows on your Following page and their releases are
    tracked.
  - *Subscribe + Notify* — same as above, plus a webhook fires when a new release
    is detected.
- **Upcoming releases** for followed artists, grouped by window: next 24h, this
  week, the following week, this month, or all.
- **Artist info** (bio, image, link) from the **Last.fm** API.
- **Upcoming albums** discovered via **MusicBrainz** release-groups (the same
  approach aurral uses), with cover art from the Cover Art Archive.
- **Settings page** to configure the music directory, API keys, webhook, check
  interval, and default theme.
- **JSON API** for everything, including the upcoming-releases feeds.
- **PWA**: installable, offline app shell, AMOLED theme color.

Only checks artists you **follow** — with ~3000 artists, MusicBrainz's ~1 req/sec
rate limit makes a full-library sweep impractical, so tracking is scoped to your
subscriptions.

## Run with Docker

1. Edit `docker-compose.yml` and point the music volume at your library:

   ```yaml
   volumes:
     - ./data:/data
     - /path/to/your/music:/music:ro
   ```

2. Build and start:

   ```bash
   docker compose up -d --build
   ```

3. Open <http://localhost:8080>.

Or with plain Docker:

```bash
docker build -t simple-music-tracker .
docker run -d -p 8080:8080 \
  -v "$(pwd)/data:/data" \
  -v "/path/to/your/music:/music:ro" \
  --name simple-music-tracker simple-music-tracker
```

The SQLite database is stored in the `/data` volume so it survives restarts.

## First-time setup

1. Go to **Settings**:
   - Confirm the **music directory** (defaults to `/music`, where the compose file
     mounts your library).
   - Add a **Last.fm API key** (free at <https://www.last.fm/api>) for artist bios
     and images. Optional but recommended.
   - Add a **MusicBrainz contact** (email or URL). MusicBrainz etiquette asks for a
     contact in the request User-Agent.
   - Optionally configure the **webhook** for Notify subscriptions.
2. On the **Artists** page, click **Scan library**. Progress shows live.
3. Subscribe to artists (checkboxes). Subscribing triggers an immediate metadata
   fetch; after that, a background job re-checks on the configured interval.

## Run locally (without Docker)

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
SMT_DB_PATH=data/tracker.db python -m app.main   # dev server on :8080
# or production:
gunicorn --workers 1 --threads 8 --bind 0.0.0.0:8080 app.main:app
```

Use a **single worker** (with threads) so the in-process scheduler and scan/refresh
progress state stay consistent.

## Webhook

For **Subscribe + Notify** artists, a webhook fires once per newly discovered
release. Configure URL, method, headers, and a JSON body template in Settings.

The body template supports these placeholders:

`{artist}` `{title}` `{release_date}` `{type}` `{image_url}`

Default payload:

```json
{
  "event": "new_release",
  "artist": "{artist}",
  "title": "{title}",
  "release_date": "{release_date}",
  "type": "{type}",
  "image": "{image_url}"
}
```

Use **Send test webhook** on the Settings page to verify your endpoint.

## JSON API

| Method | Endpoint | Description |
| ------ | -------- | ----------- |
| GET  | `/api/stats` | Library counts and upcoming totals. |
| GET  | `/api/artists` | List artists. Params: `q`, `subscription` (`none\|subscribed\|notify\|following`), `sort` (`name\|tracks\|recent`), `limit`, `offset`. |
| GET  | `/api/artists/<id>` | Artist detail with tracked releases. |
| POST | `/api/artists/<id>/subscription` | Body `{"state": "none\|subscribed\|notify"}`. |
| POST | `/api/artists/subscriptions` | Bulk: `{"ids": [...], "state": "..."}`. |
| POST | `/api/artists/<id>/refresh` | Re-fetch one artist's info/releases now. |
| GET  | `/api/subscriptions` | All followed artists. |
| GET  | `/api/upcoming` | Upcoming releases. Param `window`: `day`, `week`, `next-week`, `month`, `all`. |
| POST | `/api/scan` | Start a library scan. |
| GET  | `/api/scan/status` | Scan progress. |
| POST | `/api/refresh` | Refresh all followed artists. |
| GET  | `/api/refresh/status` | Refresh progress. |
| GET/POST | `/api/settings` | Read or update settings. |
| POST | `/api/webhook/test` | Fire a sample webhook. |
| GET  | `/api/health` | Health check. |

Example — albums dropping this week:

```bash
curl http://localhost:8080/api/upcoming?window=week
```

## How upcoming albums are found

For each followed artist the app:

1. Resolves the artist to a MusicBrainz ID (using an embedded `musicbrainz_artistid`
   tag if present, otherwise a name search).
2. Lists the artist's `album`/`ep` release-groups from MusicBrainz.
3. Keeps release-groups whose `first-release-date` is in the future or within the
   last 30 days, and stores them.
4. Cover art is linked from the Cover Art Archive.

MusicBrainz requests are globally rate-limited to ~1/sec with a descriptive
User-Agent, per their guidelines.

## Tech

- Python 3.11, Flask, SQLite (stdlib `sqlite3`), gunicorn.
- `requests` for API calls, `mutagen` for tag reading.
- Vanilla HTML/CSS/JS frontend, no build step. PWA via manifest + service worker.

## Project layout

```
app/
  main.py          Flask app: pages + JSON API
  db.py            SQLite schema, settings, helpers
  scanner.py       Music directory scanning (mutagen)
  musicbrainz.py   Upcoming-album discovery (rate-limited)
  lastfm.py        Artist info
  webhooks.py      Webhook delivery
  tracker.py       Per-artist refresh orchestration
  scheduler.py     Background periodic refresh
  templates/       Jinja2 pages
  static/          CSS, JS, manifest, service worker, icons
Dockerfile, docker-compose.yml, requirements.txt
```
