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
  Artists are written to the database **in batches as they're found**, so they
  show up in the list (and can be subscribed to) while the scan is still running.
- **Quick scan** for fast re-syncs after the first full scan: it only reads files
  added or changed since the last scan, so dropping in a few new albums syncs
  almost instantly instead of re-reading the whole library.
- **Fast subscribe/unsubscribe** via a filterable table of checkboxes — built for
  thousands of artists. Bulk-select rows to subscribe or unsubscribe in one go.
- **Ignore artists** to hide ones you don't care about (e.g. an act that no longer
  releases music) from the main library list. Ignored artists are parked on a
  separate **Ignored** page where you can un-ignore them at any time, so nothing
  is ever lost.
- **Merge artists** to combine duplicates (e.g. "Beatles" and "The Beatles") into
  one record, moving their tracks and releases together.
- **Match to MusicBrainz** by pasting an artist URL on the artist page to fix or
  set the MusicBrainz id used for release lookups.
- **Full discography on demand** — opening an artist page fetches all their
  Albums, EPs, and Singles from MusicBrainz (in that order, newest first), each
  category with a show/hide toggle. The MusicBrainz call happens only when you
  open the artist page (and is briefly cached), to keep API usage low. A setting
  lets you auto-hide chosen categories by default.
- **Two subscription levels** per artist:
  - *Subscribe* — the artist shows on your Following page and their releases are
    tracked.
  - *Subscribe + Notify* — same as above, plus a webhook fires when a new release
    is detected.
- **Per-artist release-type selection** — choose any combination of **Albums**,
  **EPs**, and **Singles** to watch for each artist (set a default for new
  follows in Settings).
- **Built-in rate limiting** — every external lookup runs through a single
  background worker, so even bulk-subscribing thousands of artists makes requests
  one at a time, paced under MusicBrainz's limits (configurable, with automatic
  backoff on 429/503). No risk of getting your instance blocked.
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

## Run the pre-built image (pull from GitHub)

A GitHub Actions workflow builds a multi-arch image (`linux/amd64` + `linux/arm64`)
and publishes it to the **GitHub Container Registry** on every push to the
default branch and every `vX.Y.Z` tag. Available tags:

- `ghcr.io/jasii/simple-music-tracker:latest` — newest default-branch build
- `ghcr.io/jasii/simple-music-tracker:v1.2.3` — a specific release tag

Pull and run it directly:

```bash
docker run -d -p 8080:8080 \
  -v "$(pwd)/data:/data" \
  -v "/path/to/your/music:/music:ro" \
  --name simple-music-tracker \
  ghcr.io/jasii/simple-music-tracker:latest
```

Or use the provided [`docker-compose.example.yml`](docker-compose.example.yml)
(edit the music path first):

```bash
docker compose -f docker-compose.example.yml up -d
# update later:
docker compose -f docker-compose.example.yml pull
docker compose -f docker-compose.example.yml up -d
```

Then open <http://localhost:8080>.

### Publishing your own image

The workflow at `.github/workflows/docker-publish.yml` runs automatically once
the code is on your default branch. To publish:

1. Merge to `main` (or push a tag like `v1.0.0`), or trigger it manually from the
   repo's **Actions** tab (it supports `workflow_dispatch` on any branch).
2. The first successful run creates the package under your repo's **Packages**.
   If you want anyone to pull without authenticating, open the package settings
   and set its visibility to **Public**.
3. Pulling a private package requires a GitHub token with `read:packages`:
   `echo $TOKEN | docker login ghcr.io -u <username> --password-stdin`.

No secrets are needed for publishing — the workflow uses the built-in
`GITHUB_TOKEN` with `packages: write` permission.

## Build and run with Docker (local source)

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
2. On the **Artists** page, click **Full scan**. Progress shows live and artists
   appear in the list as they're discovered. Later, when you add music, use
   **Quick scan** to pick up just the new files.
3. Subscribe to artists (checkboxes). Subscribing triggers an immediate metadata
   fetch; after that, a background job re-checks on the configured interval.

You can also **monitor an artist that isn't in your library** by pasting a
MusicBrainz artist link (e.g. `https://musicbrainz.org/artist/<mbid>`) or a raw
artist ID into the "Monitor an artist by MusicBrainz link" box on the Artists
page. The artist is looked up on MusicBrainz, added, and followed immediately.

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
| GET  | `/api/artists` | List artists. Params: `q`, `subscription` (`none\|subscribed\|notify\|following`), `ignored` (`0` hide ignored — default, `1` only ignored, `all`), `sort` (`name\|tracks\|recent`), `limit`, `offset`. |
| GET  | `/api/artists/<id>` | Artist detail with tracked releases. |
| POST | `/api/artists/<id>/subscription` | Body `{"state": "none\|subscribed\|notify"}`. |
| POST | `/api/artists/add` | Monitor an artist from a MusicBrainz link/ID. Body `{"link": "https://musicbrainz.org/artist/<mbid>", "state": "subscribed\|notify", "types": ["album","ep","single"]}`. Creates the artist if not in the library. |
| POST | `/api/artists/<id>/monitor-types` | Set watched release types. Body `{"types": ["album","ep","single"]}` (non-empty subset). |
| POST | `/api/artists/<id>/ignore` | Hide/unhide an artist. Body `{"ignored": true\|false}`. |
| POST | `/api/artists/ignore` | Bulk hide/unhide. Body `{"ids": [...], "ignored": true\|false}`. |
| GET  | `/api/ignored` | List ignored artists. |
| POST | `/api/artists/<id>/mbid` | Match an artist to a MusicBrainz URL/ID. Body `{"link": "https://musicbrainz.org/artist/<mbid>"}`. |
| POST | `/api/artists/<id>/merge` | Merge other artists into this one. Body `{"source_ids": [...]}`. |
| GET  | `/api/artists/<id>/discography` | All albums/EPs/singles from MusicBrainz (on-demand), grouped and ordered. |
| POST | `/api/artists/subscriptions` | Bulk: `{"ids": [...], "state": "..."}`. |
| POST | `/api/artists/<id>/refresh` | Re-fetch one artist's info/releases now. |
| GET  | `/api/subscriptions` | All followed artists. |
| GET  | `/api/upcoming` | Upcoming releases. Param `window`: `day`, `week`, `next-week`, `month`, `all`. |
| POST | `/api/scan` | Start a library scan. Body `{"quick": true}` for an incremental sync of only added/changed files. |
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

Each artist is checked only for the release types you selected (Albums / EPs /
Singles).

## Rate limiting

All MusicBrainz/Last.fm work is funneled through a **single background worker**
draining a job queue, so no matter how many artists you bulk-subscribe, requests
are made one at a time rather than fanning out into thousands of concurrent
calls. This is the equivalent of aurral's MusicBrainz limiter
(`maxConcurrent: 1, minTime: 1000`).

- **MusicBrainz**: globally paced with a configurable minimum gap (default and
  floor `1000ms`, i.e. 1 request/second, matching aurral), a descriptive
  User-Agent, and retries with exponential backoff (`300ms · 2^n`, honoring any
  `Retry-After` header) on transient connection errors and `429`/`500`/`502`/
  `503`/`504`. A `404` is treated as "not found" and not retried.
- **Last.fm**: a 6s timeout with up to 2 retries and small backoff (aurral's
  values). Calls are serialized through the same worker, so no separate
  concurrency limiter is needed.

Compared with aurral we deliberately skip its short-TTL response cache (we
persist MusicBrainz IDs in the database and only refresh on a long interval, so
repeat lookups are rare) and its multi-provider failover/health-probe machinery
(out of scope for a single-instance app).

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
