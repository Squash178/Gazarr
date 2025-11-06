# Deploying on Unraid

1. Copy the repository to your Unraid server (eg. `/mnt/user/appdata/gazarr`).
2. Edit `backend/.env` if you want to override defaults such as database path or Torznab timeout.
3. Adjust `docker-compose.yml` if the default port mapping (`8000` API, `5173` frontend) conflicts with existing services.
4. From the repo directory run:

   ```bash
   docker compose up -d
   ```

   This creates:

   - `gazarr-api` – FastAPI app with the SQLite database persisted to `./data/backend`
   - `gazarr-frontend` – SvelteKit build served via Nginx hitting the API

5. In Prowlarr (or your chosen Torznab indexer aggregator) create an application entry for Gazarr and add the generated URL/API key to the Gazarr provider list.

## Tips

- Bind the repository folder into Unraid’s “User Templates” section if you prefer managing stacks through the web UI.
- The API service reads `GAZARR_` environment variables, so you can set them straight in Unraid’s compose override (eg. adding `GAZARR_TORZNAB_MAX_AGE_DAYS=30`).
- Rebuild the `frontend` service if you update `PUBLIC_API_BASE` or adjust the UI (run `docker compose build frontend`).
- The SQLite database file lands in `data/backend/app.db`. Back up this folder or redirect `GAZARR_DATABASE_URL` to a Postgres instance once multi-user access is needed.
