# Gazarr Frontend

SvelteKit interface for managing providers, magazines, and running quick searches against the Gazarr API.

## Local development

```bash
cd frontend
npm install
npm run dev -- --host
```

Set `PUBLIC_API_BASE` in `.env` (see `.env.example`) to point at the Gazarr backend.

## Production build

```bash
npm run build
```

The Dockerfile builds and serves the static output via Nginx.
