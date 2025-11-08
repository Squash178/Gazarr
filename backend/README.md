# Gazarr Backend

FastAPI + SQLModel service that stores magazine metadata and proxies simple Torznab/Newznab searches (eg. Prowlarr).

## Quickstart

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r backend/requirements.txt
cp backend/.env.example backend/.env  # adjust values
uvicorn app.main:app --app-dir backend/app --reload
```

The API exposes:

- `GET /providers` / `POST /providers`
- `GET /magazines` / `POST /magazines`
- `POST /magazines/search` – hits enabled providers and returns NZB metadata
- `GET /sabnzbd/config` / `PATCH /sabnzbd/config` – manage SABnzbd connection details
- `GET /sabnzbd/status` – returns whether SABnzbd integration is configured
- `GET /downloads` – lists items currently in the watched downloads folder
- `POST /sabnzbd/download` – forwards an NZB URL to SABnzbd
- `POST /sabnzbd/test` – validates the SABnzbd configuration/API key
- `GET/POST/DELETE /providers/{id}/categories` – manage Torznab/Newznab categories per provider
- `GET/PUT /magazines/{id}/categories` – assign provider categories to magazines

## Seeding data

```bash
python -m app.seed \
  --provider-name prowler \
  --provider-url https://prow.example/api \
  --provider-key SECRET \
  --magazine-title "Linux Format"
```

## Running inside Docker

See the project level `docker-compose.yml` for a production-ready container using Uvicorn.

## SABnzbd integration

Set the following environment variables (see `.env.example`) to enable SABnzbd connectivity (they can be edited later in the UI):

- `GAZARR_SABNZBD_URL` – base URL to your SABnzbd instance, e.g. `http://localhost:8080/sabnzbd`
- `GAZARR_SABNZBD_API_KEY` – the API key from SABnzbd
- Optional: `GAZARR_SABNZBD_CATEGORY`, `GAZARR_SABNZBD_PRIORITY`, `GAZARR_SABNZBD_TIMEOUT`

Use `POST /sabnzbd/test` to verify the connection. The dashboard exposes a SABnzbd settings card where you can edit credentials, save them, and run the built-in connection test.

## Download monitor

Configure the automatic download mover by setting:

- `GAZARR_DOWNLOADS_DIR` – the SABnzbd completed downloads folder to watch.
- `GAZARR_LIBRARY_DIR` – the destination folder for processed issues.
- `GAZARR_STAGING_DIR` – optional staging area used to clean files before they land in the library.
- Optional: `GAZARR_DOWNLOADS_POLL_INTERVAL`, `GAZARR_DOWNLOADS_SETTLE_SECONDS` to tune polling behaviour.
- Optional: `GAZARR_DEBUG_LOGGING=true` (and the dashboard toggle) to dump detailed SABnzbd queue/history snapshots each tracker cycle.

When the directories are configured the backend spawns two background tasks on startup:

- A filesystem monitor that watches the SABnzbd completed folder. Once a download has “settled” it is copied into the staging directory, stripped of third‑party PDF metadata, re-tagged with Gazarr details, renamed using the parsed issue code, a cover thumbnail is rendered, and the cleaned folder is finally moved into the library.
- A SABnzbd tracker that polls the queue/history endpoints and records progress for each job so the UI can report intermediate stages (queued, downloading, processing, completed, moved).

Use `GET /downloads` to inspect both the filesystem queue and the tracked SABnzbd jobs.
The dashboard lets you clear the entire log or remove individual download entries if something gets stuck.

## Automatic downloads

Gazarr can automatically look for new issues and enqueue them in SABnzbd. Use the **Settings → Auto downloader** panel in the dashboard to toggle the background job, adjust the scan interval, and limit how many issues are queued per magazine. (The `GAZARR_AUTO_DOWNLOAD_*` variables only seed the initial defaults.)

Each magazine can also be given an “auto-download start” year and issue number from the dashboard. Gazarr will ignore any releases at or before that marker so you can seed the catalog at a known point (e.g. “start at 2023 issue #350”) without bulk-downloading older history.

Need an immediate refresh? Hit the “Scan auto downloads” button in the dashboard header to run the background search loop on demand.

You can also enable the “Auto fail stuck downloads” option in the same settings panel. When active, Gazarr watches SABnzbd queue progress and automatically marks any job as failed if it hasn’t reported progress within your chosen number of minutes.
