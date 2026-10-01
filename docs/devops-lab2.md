# DevOps lab 2: Continuous Integration

The lab uses the copied microservice project and its existing Dockerfiles.
The GitHub Actions workflow is [.github/workflows/ci.yml](../.github/workflows/ci.yml).

## Workflow

Every pull request and every branch push runs CI. `workflow_dispatch` also allows
a manual rerun. The PR's Checks tab shows each job as an individual check.

Six parallel `Python / ...` jobs cover user, license, usage, distribution,
telegram-bot and discord-bot. Each installs the pinned runtime/test dependencies,
compiles its own Python source (build), runs blocking Ruff analysis, and executes
the component's real tests. License and Usage also run their own Redis cache and
HTTP contract variants. The matrix has `fail-fast: false`, so one failure cannot
cancel the remaining component checks. `Web` independently runs `npm ci`,
`npm run build`, blocking `npm run lint` and `npm test`.

After every Python and Web check succeeds, seven `Build image / ...` jobs build
each existing Dockerfile on a runner. This stage uses local runner tags only
and does not access the registry. Python dependencies and npm packages use the
GitHub Actions dependency caches from `setup-python` and `setup-node`.

On branch pushes only, seven `Publish image / ...` jobs log in to GHCR using the
short lived GitHub Actions `GITHUB_TOKEN` (`packages: write` is scoped to that
job). No registry credentials are stored in Git. Each image receives
`sha-<full commit SHA>`. Only pushes to `main` also receive `latest`.

The seven `Verify published / ...` jobs pull the exact SHA image and run it.
The Web image starts nginx and answers an HTTP request; the Python images execute
Python inside the published image. A full service startup requires the external
databases, RabbitMQ and environment values and is demonstrated separately with
Compose. The CI smoke check verifies image publishing and pull/run, not that
the whole production stack has started.

Images are named `ghcr.io/mtgmods/labs-license-management-platform-<component>`
where `<component>` is `user`, `license`, `usage`, `distribution`, `telegram-bot`,
`discord-bot` or `web`. The package list is visible at
[the GitHub organization Packages page](https://github.com/MTGMODS?tab=packages).
To reproduce one image check, use the exact tag displayed by GitHub Actions:

```bash
docker pull ghcr.io/mtgmods/labs-license-management-platform-web:sha-<40-character-commit-sha>
docker run --rm -p 127.0.0.1:18080:8080 ghcr.io/mtgmods/labs-license-management-platform-web:sha-<40-character-commit-sha>
# From another terminal: curl -f http://127.0.0.1:18080/
```

If the package is private, authenticate with GitHub first. For the whole
application, use the repository's `docker-compose.yml` plus the local `.env`
files and private VIP build inputs described in the main README.

## Branch protection and failed-PR demonstration

Protect `main` with required pull requests, no admin bypass, no force pushes or
deletions, and these 14 PR checks:

- `Python / user`
- `Python / license`
- `Python / usage`
- `Python / distribution`
- `Python / telegram-bot`
- `Python / discord-bot`
- `Web`
- `Build image / user`
- `Build image / license`
- `Build image / usage`
- `Build image / distribution`
- `Build image / telegram-bot`
- `Build image / discord-bot`
- `Build image / web`

`Publish image / ...` and `Verify published / ...` are intentionally absent
from the required PR checks: they run only on branch pushes and would not be
available for a pull request created from a fork.

For a blocked-PR demonstration, create a separate branch with a temporary
Ruff error (for example an unused `import os` in `services/usage/main.py`),
push it and open a PR to `main`. The `Python / usage` check must fail, the
image-build matrix must not run, and GitHub must mark the PR as blocked by
required checks. Leave this example branch unmerged. Revert the error when
the demonstration is no longer needed.

## Local reproduction

```bash
python -m pip install -r tests/requirements.txt ruff==0.13.3
python -m ruff check services bots
python -m pytest tests -q
cd web && npm ci && npm run build && npm run lint && npm test
```

These tests use isolated SQLite/fakeredis and mocked platform APIs. Live
PostgreSQL, RabbitMQ and Telegram/Discord integrations need additional
integration testing; the workflow does not access production credentials.
