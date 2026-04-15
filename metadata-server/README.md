# metadata-server

FastAPI + Postgres metadata service for Creepy Pasta TTS runs. Deployed on the home server behind Twingate.

## Quick start

```bash
cd metadata-server
cp .env.example .env
# Edit .env — set METADATA_API_KEY, POSTGRES_PASSWORD, etc.

# Start postgres + app
docker compose up -d

# Run migrations
docker compose exec metadata-svc alembic upgrade head
```

## Development

```bash
pip install -e ".[dev]"
# Set DATABASE_URL in .env or environment
alembic upgrade head
uvicorn app.main:app --reload
```

## Type checking

```bash
mypy app
```

## Twingate setup

1. Install a Twingate Connector on the home server following the Twingate docs.
2. Add a Resource for `metadata-svc:8080` (or the host IP/port).
3. The RunPod Pod installs the headless Twingate client and connects via `TWINGATE_NETWORK` + `TWINGATE_ACCESS_TOKEN` env vars.

## Operator commands

### Start/stop services

```bash
# Start services (postgres + metadata-svc)
docker compose up -d

# Stop services
docker compose stop

# Stop and remove containers
docker compose down
```

### Database migrations

```bash
# Apply all pending migrations
docker compose exec metadata-svc alembic upgrade head

# Check current migration version
docker compose exec metadata-svc alembic current

# Create a new migration (after changing models)
docker compose exec metadata-svc alembic revision --autogenerate -m "description"
```

### Backup and restore

**Postgres backup:**
```bash
# Create backup
docker compose exec postgres pg_dump -U metadata metadata | gzip > backup-$(date +%Y%m%d-%H%M%S).sql.gz

# Restore backup
gunzip < backup.sql.gz | docker compose exec -T postgres psql -U metadata metadata
```

**Audio directory backup:**
```bash
# Backup audio files (adjust path to your AUDIO_STORAGE_ROOT)
rsync -av /var/lib/creepy_pasta/audio/ /backup/location/audio/

# Or with rclone (for remote backup)
rclone sync /var/lib/creepy_pasta/audio/ remote:creepy-pasta-audio/
```

### Health checks

```bash
# Check service health
curl http://localhost:8080/healthz

# Via Twingate (after setup)
curl http://metadata-svc.local:8080/healthz

# View logs
docker compose logs -f metadata-svc
docker compose logs -f postgres
```
