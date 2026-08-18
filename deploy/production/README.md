# Production Docker deployment

This stack exposes a single HTTP gateway and keeps Explorer, document extraction,
and FalkorDB on the private Docker network.

## Required local files

Create these files beside `docker-compose.yml`; do not commit them:

- `.env`
  - `PUBLIC_PORT=9009`
  - `PUBLIC_ORIGIN=http://your-host:9009`
  - Optional: `PYPI_INDEX_URL=https://your-nearby-pypi-mirror/simple`
- `.htpasswd`
  - Generate with `openssl passwd -apr1` or `htpasswd`.
  - On Linux bind-mount deployments, run `chmod 644 .htpasswd` so the
    unprivileged Nginx worker can read the password hashes.

## Build and start

```bash
docker compose build explorer
docker compose build document-extractor
docker compose up -d
```

The gateway routes `/` to Explorer and `/extractor/` to the extraction service.
Only the gateway port is published on the host.
