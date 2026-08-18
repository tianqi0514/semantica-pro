# Production Docker deployment

This stack exposes a single HTTP gateway and keeps Explorer, document extraction,
and FalkorDB on the private Docker network.

## Required local files

Create these files beside `docker-compose.yml`; do not commit them:

- `.env`
  - `PUBLIC_PORT=9009`
  - `PUBLIC_ORIGIN=http://your-host:9009`
- `.htpasswd`
  - Generate with `openssl passwd -apr1` or `htpasswd`.

## Build and start

```bash
docker compose build explorer
docker compose build document-extractor
docker compose up -d
```

The gateway routes `/` to Explorer and `/extractor/` to the extraction service.
Only the gateway port is published on the host.
