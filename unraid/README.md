Unraid Docker Template for Gazarr
=================================

This folder contains an Unraid Community Applications (CA) Docker template for the single-container Gazarr image.

Quick use (local/private):
1) Copy this repo to your Unraid box (e.g., /mnt/user/appdata/gazarr).
2) In Unraid UI → Docker tab → Add Container → Template repositories → Add new URL to this template folder or use "Load template from file" if available.
3) Select "Gazarr" template, adjust share paths and apply.

Recommended (public CA listing):
1) Host this XML in a public git repo (commonly a dedicated "docker-templates" repo).
2) Ensure the <Repository> points to a published image (e.g., ghcr.io/youruser/gazarr:main).
3) In Unraid, add your template repo URL under Apps → Settings → Template Repositories.

Notes:
- Web UI: http://[IP]:[PORT:8000]/
- Volumes map /app/data, /downloads, /library, /staging to your Unraid shares.
- Environment variables mirror docker-compose defaults.


