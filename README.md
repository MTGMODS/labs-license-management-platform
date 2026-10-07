# MTG MODS platform

Micro-SaaS around a freemium digital product: identity, licenses, usage analytics, protected file delivery, public web app, Telegram and Discord adapters.

```
├── services/user/           # OAuth, JWT, profiles
├── services/license/        # keys, HWID, tariffs, sales, bot API
├── services/usage/          # launch telemetry, public stats
├── services/distribution/   # one-shot VIP file download
├── web/                     # SPA (cabinet, open stats, admin)
├── bots/telegram/           # Stars, VIP chat, Mini App entry
├── bots/discord/            # VIP role / DMs
└── docker-compose.yml
```

APIs talk over HTTP and RabbitMQ. Bots and license↔user stay on the **Docker network** (not through public nginx). The browser and Telegram Mini App still use `https://api.mtgmods.com` / `https://mtgmods.com`.

## Stack

Python 3.12, FastAPI, PostgreSQL 16 (one database per service), Redis 7.4, RabbitMQ 3.13, React 19 + Vite, Docker Compose.

## Run

Each unit has `.env.example`. Copy and align secrets **before** `up`:

```bash
cp services/user/.env.example services/user/.env
cp services/license/.env.example services/license/.env
cp services/usage/.env.example services/usage/.env
cp services/distribution/.env.example services/distribution/.env
cp bots/telegram/.env.example bots/telegram/.env
cp bots/discord/.env.example bots/discord/.env
```

Must match across files:

- `JWT_SECRET` — user + license
- `INTERNAL_SECRET_TOKEN` — user + license
- `BOT_SECRET_TOKEN` — license + both bots
- RabbitMQ user/password — license `.env` (broker) + `RABBITMQ_URL` in license, distribution, bots (`@rabbitmq`)

VIP template and obfuscator are **not** in git. On the server, overlay:

`services/distribution/app/builds/vip/` and `services/distribution/app/tools/`

then rebuild `distribution-service`.

```bash
docker compose up --build
```

The Compose project name stays `mtgmods_backend` so existing Postgres volumes keep their names.

| Service | URL |
|---------|-----|
| User | http://localhost:8001/health |
| License | http://localhost:8002/health |
| Usage | http://localhost:8003/health |
| Distribution | http://localhost:8005/health |
| Web | http://localhost:8080 |
| RabbitMQ UI | http://localhost:15672 |

OAuth redirect URIs for local compose use host port **8001**. Production callbacks: `https://api.mtgmods.com/v1/users/auth/...` (see comments in `services/user/.env.example`).

Bots call `http://license-service:8000/api/v1/license/...`. After switching from standalone bot containers, stop the old ones — two processes with the same Telegram token will fight.

`web` bakes `VITE_API_URL` at **image build** (default `https://api.mtgmods.com`). Override: `VITE_API_URL=... docker compose build web`.

## Local API without Docker

License and Usage use `REDIS_URL` (default `redis://redis:6379/0` in Compose).
For a locally installed Redis, use `redis://127.0.0.1:6379/0`.
Without Redis, public statistics are computed from the database on each request.

```bash
cd services/user   # or license / usage / distribution
python -m venv venv
venv/Scripts/activate   # Linux: source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env    # set DEBUG_MODE=True and SQLite URLs
fastapi dev main.py --port 8001
```

Ports: **8001 / 8002 / 8003 / 8005**. Vite for the SPA: `cd web && npm ci && npm run dev` (port 5173; optional `VITE_DEV_*_TARGET` in `web/.env.example`).

## Public statistics cache

License and Usage share Redis with separate keys:

- `mtgmods:license:public_stats:v1`
- `mtgmods:usage:public_stats:v1`

Both public endpoints return their statistics directly. Redis stores an internal
JSON envelope with `data` and `fresh_until`; this envelope is not exposed by the API.
Results are fresh for 5 minutes and retained for up to 24 hours. A request for
stale statistics returns the previous result immediately and schedules a refresh
in the serving FastAPI process, with a separate database session.

A per-key Redis lock coordinates both background refreshes and cold starts across
processes. The lock lasts 120 seconds; computation is limited to 90 seconds.
Publication and release check the owner's token atomically, so an old worker
cannot overwrite a newer result or release another worker's lock. If a process
dies, the lock expires and a later request retries the refresh.

On a cold cache, the lock owner computes the first response. Other requests wait
up to 4 seconds, then receive HTTP 503 with `Retry-After: 2` if data is still
unavailable. The web client has a 5-second stats timeout, so the first visit after
a cache reset can require a retry. Failed background refreshes preserve old data.
Redis connection/command timeouts are 0.5 seconds with no automatic retries;
when Redis is unavailable, statistics fall back to uncached DB reads. This keeps
the endpoint usable but can increase DB load during a Redis outage.

Redis has no published host port or configured persistence; restarting it clears
this rebuildable cache. API container restarts keep using the existing Redis data.
Health endpoints do not require Redis. There is no timer: refreshes are driven by
requests. The heavy SQL and the public response shapes are unchanged.

Existing production deployment (PostgreSQL and the other services already running):

```bash
docker compose up -d redis
docker compose up -d --build --no-deps license-service usage-service
docker compose exec -T redis redis-cli ping
curl -fsS http://127.0.0.1:8002/api/v1/license/stats/public
curl -fsS http://127.0.0.1:8003/api/v1/usage/stats/public
docker compose exec -T redis redis-cli --scan --pattern 'mtgmods:*:public_stats:v1*'
```

The default Redis URL works with existing service `.env` files; set `REDIS_URL`
in both files only to override it. No database migration is needed.

## Automated tests

For the DevOps Continuous Integration lab, see
[docs/devops-lab2.md](docs/devops-lab2.md) for workflow jobs, GHCR tags,
branch protection checks and the blocked-PR demonstration.

Regression tests cover all four backend services, both bots and the web client.
Python tests use isolated SQLite databases, temporary files, fakeredis with Lua
support and mocked external APIs; no production credentials or running Docker
are required. Install test dependencies in a virtual environment:

```bash
python -m pip install -r tests/requirements.txt
python -m pytest tests -q
```

Web tests (HTTP/auth/session logic, API contracts and React route guards):

```bash
cd web
npm ci
npm test
```

See [tests/README.md](tests/README.md) for setup, per-component commands, covered
scenarios and explicit limitations. These tests do not replace live
PostgreSQL/RabbitMQ integration or real-browser end-to-end testing.

## Stateless architecture lab

The High-Load / ІВСАВПЗ Lab 2 uses an isolated two-instance License Service
deployment with a shared PostgreSQL/Redis state layer, a separate expiry worker,
an Nginx load balancer and `X-Instance-ID` tracing. The state audit, updated C4
diagram, PowerShell deployment steps, cross-instance CRUD flow and instance-loss
scenario are documented in [docs/stateless-lab2.md](docs/stateless-lab2.md).

```powershell
docker compose -f docker-compose.stateless.yml up --build -d
```

## Horizontal scaling lab

High-load / ІВСАВПЗ Lab 3 extends the stateless License Service into a private
three-node upstream pool behind one Nginx entry point. It includes switchable
Round Robin and Least Connections configs, a synthetic slow-node experiment,
database-aware readiness, node-failure recovery and measured 1/2/3-instance
results. See [docs/highload-lab3.md](docs/highload-lab3.md).

```powershell
docker compose -f docker-compose.scaling.yml up --build -d
```

## Kubernetes orchestration lab

DevOps Lab 3 deploys two License Service replicas plus PostgreSQL and Redis to a
local Minikube cluster. The repository includes one manifest per Kubernetes
object, health probes, resource constraints, ConfigMap/Secret configuration,
persistent storage, Nginx Ingress, immutable GHCR image versions and reproducible
self-healing, scaling, rolling-update, rollback and failure-diagnostics scenarios.
See [docs/devops-lab3.md](docs/devops-lab3.md).

```powershell
kubectl apply -k k8s
```

## Helm packaging lab

DevOps Lab 4 converts the Kubernetes baseline into a reusable Helm chart with
release-derived names, parameterized dev/prod environments, optional Ingress and
PVC, shared helpers, install notes, a pre-upgrade health hook and `helm test`.
The verified release lifecycle is documented in
[docs/devops-lab4.md](docs/devops-lab4.md), and the values table is in the
[chart README](helm/mtgmods-license/README.md).

```powershell
helm lint helm/mtgmods-license
helm upgrade --install lab-license helm/mtgmods-license -n mtgmods-helm -f helm/mtgmods-license/values-dev.yaml --wait --wait-for-jobs
```

## License

MIT — see `LICENSE` in the repository root.
