# Automated regression tests

These tests exercise business rules and error paths, not just application startup.
They do not require production credentials, Docker or running services.

## Python: four services and two bots

Use Python 3.12 (the version used to verify this suite), in an isolated environment:

```bash
python -m venv .venv
# Windows PowerShell:
.venv/Scripts/python -m pip install -r tests/requirements.txt
.venv/Scripts/python -m pytest tests -q
# Linux/macOS: use .venv/bin/python instead.
```

When your virtual environment is already activated:

```bash
python -m pytest                              # All Python tests
python -m pytest tests/system/test_license.py # One component
python -m pytest tests/system/test_telegram.py tests/system/test_discord.py
python -m pytest tests/system -m integration  # Real DB/files/API, external services mocked
python -m pytest tests/system -m unit         # Bot handlers and expiration publisher
python -m pytest tests --junitxml=test-results/python.xml
```

The test requirements include each component's pinned runtime dependencies. Tests
live outside Docker build contexts and do not add dependencies to production images.
Every component has an `app` package, so the module-scoped fixture switches import
paths and unloads that namespace between test modules. Do not import `app` at test
collection time: use the `component` fixture or import inside the test/fixture.

### Coverage by scenario

- **License:** purchase/key generation, activation and forced replacement, rejecting
  reused keys, finite/forever expiry, banned/expired/inactive keys, HWID registration
  and limits, repeat-device use, reset quotas and ownership, dashboard masking,
  expiration SQL, cascading deletion, admin duration/ban updates, paid-statistics
  filters, download authorization and RPC failure, HTTP validation/authentication.
- **User:** new/repeat social login, nickname handling, blocked accounts, linking
  and unlinking providers, preventing social-account reuse, retaining one provider,
  hashed refresh-session storage, token rotation/replay/logout, one-use/expired
  OAuth handoffs, JWT type/signature/expiration, admin access, link tickets,
  Telegram Mini App signatures/timestamps, HTTP profile and S2S authorization.
- **Usage:** launch validation, server ranges and `server_id` alias, committed
  writes read in a separate DB session, distinct HWID aggregation, VIP counts,
  time-window boundaries and empty statistics.
- **Distribution:** temporary CP1251 Lua template, timed and forever generation,
  missing-template failure, file response and post-download deletion, invalid
  tokens, old-file cleanup without deleting templates or fresh builds.
- **Expiration worker:** commit before commands, correct social IDs, both/single/no
  linked provider, Telegram and Discord routing keys.
- **Telegram:** current tariff and Stars amount checks, unavailable/missing tariff,
  malformed JSON, invoice construction, key generation success/failure, VIP chat
  admission, ignoring unrelated chats, FOREVER display, Rabbit callback dispatch
  and soft-kick sequence.
- **Discord:** public `/vip` response and native timestamps, role authorization,
  avoiding repeated grants, Rabbit role removal including cache-miss member fetch,
  direct messages and missing recipient IDs.
- **Redis/public stats:** the existing parametrized cache suite tests both copies:
  shared fresh/stale data, concurrent refresh and cold starts, busy cache, failed
  refresh, lease ownership, malformed data, cancellation/timeouts and Redis outage.
  HTTP tests also check direct response envelopes and reuse after app restart.

### Isolation and limits

Database tests create a fresh SQLite schema for each test using the production ORM
models and repositories. SQLite is already supported in the services' debug mode.
The DB and generated files live under pytest temporary directories. Settings are
synthetic; working directories are isolated from `.env` and bot dotenv loading is
disabled. External socket connections are blocked for the component tests.

RabbitMQ channels, Telegram/Discord API calls and cross-service HTTP clients are
mocked. This verifies our routing/payload/handler logic, **not** live delivery,
broker reconnection, platform permissions, PostgreSQL-specific locking or the
private obfuscator. Redis uses fakeredis with Lua support. The existing Redis
outage test uses a loopback socket only.

This is a regression baseline across all components, not a claim of 100% line,
branch or end-to-end coverage. Real Docker/PostgreSQL/RabbitMQ integration, OAuth
provider exchanges, concurrent transaction races and browser/mobile download E2E
remain separate tests to add. Never point these tests at production services.

## Web

```bash
cd web
npm ci
npm test
npm run test:watch
```

`npm test` first type-checks tests, then runs Vitest with jsdom. Tests cover HTTP
serialization/authentication, single-flight refresh, rejected/offline sessions,
timeouts/cancellation, token storage fallback, auth-store transitions, direct
public stats payloads, license activation/download/reset requests, error mapping,
expiry/date boundaries and React private/admin route guards. Unexpected fetch
calls fail rather than accessing the development proxy or production API.

These are unit and component tests, not screenshot or real-browser E2E tests.
