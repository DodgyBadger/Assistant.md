# Development Setup

AssistantMD supports development on either a general-purpose host or the
repository devcontainer. A host can be bare metal, WSL, a VM, or a persistent
coding container. Both entrypoints use the same `scripts/dev` commands and
create an isolated `.venv` inside the checkout.

Production deployment remains container-based. See the
[Installation Guide](../setup/installation.md) for production setup.

## Choose an entrypoint

### General-purpose host

Install these host-level prerequisites:

- Git
- [UV](https://docs.astral.sh/uv/getting-started/installation/)
- Node.js 22 with npm
- A compiler/build toolchain
- `curl` and `ripgrep`

Python does not need to be installed globally. UV installs the repository-pinned
Python 3.13 interpreter. GitHub CLI and coding-agent CLIs are useful contributor
tools but are not required to run AssistantMD.

On Debian or Ubuntu, the basic system packages can be installed with:

```bash
sudo apt-get update
sudo apt-get install -y build-essential ca-certificates curl git ripgrep
```

Install Node.js 22 using the supported method for the host operating system.

### Devcontainer

Open the checkout in an editor with Dev Containers support and select
**Reopen in Container**. The container supplies UV, Node.js, Git, GitHub CLI,
build tools, and the native libraries needed by Playwright. Its post-create
step runs the same checkout setup described below, including browser setup.

The devcontainer has additional image, editor-server, memory, and disk overhead.
Use the general-purpose-host entrypoint when those tradeoffs are not worthwhile.

## Clone and set up the checkout

```bash
git clone https://github.com/DodgyBadger/Assistant.md.git
cd Assistant.md
scripts/dev setup
```

Create a local encryption key before starting the application. The same
command used by production installations works on Linux:

```bash
openssl rand -base64 32 | tr '+/' '-_' | tr -d '='
```

Copy the generated value into `.env` as `ASSISTANTMD_SECRETS_KEY`.

`scripts/dev setup` checks for this configuration and prints the command when
it is missing, but does not generate or overwrite `.env`. The encrypted secrets
store initializes—and any legacy `system/secrets.yaml` values migrate—when the
application next starts with `scripts/dev run`. After AssistantMD verifies the
encrypted values, it preserves the legacy file as
`system/migration_backups/secrets.yaml.bak` for
rollback. If that name exists, it uses the next available numbered name, such as
`secrets.yaml.bak (2)`; backups are not used by the current runtime.

`.env.example` documents the required names but contains no usable key.
`scripts/dev run` loads `.env` when it exists. A missing or unusable key leaves
the application in secrets-locked mode: the UI remains available for diagnosis,
but providers and models are unavailable and secret state is not migrated or
mutated.

When development is accessed through a TLS reverse proxy rather than directly
on loopback, add its browser-visible origin to `.env`:

```text
ASSISTANTMD_PUBLIC_URL=https://assistant-dev.example.com
```

Direct `http://127.0.0.1:8000` development does not require this setting.

`scripts/dev setup` is idempotent. It:

- installs or locates UV-managed Python 3.13;
- creates or repairs `.venv`;
- syncs all locked Python and development dependencies;
- installs frontend dependencies from `package-lock.json`;
- builds `static/output.css`;
- creates ignored `data/` and `system/` runtime directories; and
- verifies Python 3.13 and the Logfire import.

Run it again after dependency changes or when `.venv` is stale or broken. The
environment is generated state and should not be repaired manually.

To include the Playwright Chromium browser and its host libraries:

```bash
scripts/dev setup --browser
```

Installing Playwright host libraries may require `sudo` on bare metal. The
devcontainer performs this step as part of its post-create setup.

## Run AssistantMD

```bash
scripts/dev run
```

Without an authentication option, `scripts/dev run` launches AssistantMD directly
as a host process in `loopback` mode and listens on `127.0.0.1:8000`. This is the
primary use of `loopback`; it is not the mode used by the standard Docker Compose
installation. Open <http://127.0.0.1:8000/>.

By default, the command stores development runtime state under the checkout:

```text
<checkout>/
├── data/
└── system/
```

These directories are ignored by Git and the container build context. They
contain local vault data, settings, secrets, logs, and databases. Production
containers use the separate `/app/data` and `/app/system` mounts.

Select another authentication mode when the server must accept connections from
outside its own network namespace:

```bash
scripts/dev run --auth-mode trusted_proxy
scripts/dev run --auth-mode owner_token
scripts/dev run --auth-mode disabled
```

`loopback` listens on `127.0.0.1` by default. The other explicit modes listen on
`0.0.0.0`, allowing a reverse proxy or another container to reach the server.
Authentication and host network controls determine which requests are admitted.

Override the mode's default address or port when needed:

```bash
scripts/dev run --auth-mode trusted_proxy --address 127.0.0.1 --port 8080
scripts/dev run --auth-mode disabled -a 192.0.2.10 -p 8080
```

The equivalent environment variables remain available for persistent shell or
automation configuration. `ASSISTANTMD_DEV_RUNTIME_ROOT` selects an alternate
parent containing `data/` and `system/`, which is useful for an isolated or
disposable development runtime:

```bash
ASSISTANTMD_DEV_HOST=127.0.0.1 \
ASSISTANTMD_DEV_PORT=8080 \
ASSISTANTMD_DEV_RUNTIME_ROOT=/path/to/dev-state \
scripts/dev run
```

For example, the former checkout-local isolated layout remains available with:

```bash
ASSISTANTMD_DEV_RUNTIME_ROOT="$PWD/.runtime" scripts/dev run
```

Command-line address and port options take precedence over these environment
variables. Pass advanced Uvicorn arguments after `--`:

```bash
scripts/dev run -p 8080 -- --log-level debug
```

`CONTAINER_DATA_ROOT` and `CONTAINER_SYSTEM_ROOT` may override the individual
runtime paths. Explicit values take precedence over the checkout defaults or
`ASSISTANTMD_DEV_RUNTIME_ROOT`.

### Run the advanced shell during development

Start the persistent development container from the checkout:

```bash
scripts/start_advanced_shell_development.sh
```

When AssistantMD runs directly on the same host, the script's defaults need no
changes. When AssistantMD runs inside a development container, publish the SSH
port on an address that container can reach and set the corresponding host:

```bash
ADVANCED_SHELL_BIND_ADDRESS=0.0.0.0 \
ADVANCED_SHELL_CLIENT_HOST=<docker-host-gateway-address> \
scripts/start_advanced_shell_development.sh
```

Add the values printed by the script to `.env`, then restart AssistantMD. This
development-only publication can expose SSH beyond the host; restrict it with
the host firewall.

## Deploy a development branch with Docker Compose

Use this workflow to exercise a branch in a production-shaped deployment rather
than running AssistantMD through `scripts/dev`. It uses the normal persistent
vault and system mounts while building images from the checked-out branch.

1. Back up the mounted vaults, `system/`, `.env`, and any local Compose override.
   A branch may run database migrations, so rolling back can require restoring
   `system/` as well as switching Git branches.

2. Check out and update the branch:

   ```bash
   git fetch origin
   git switch <branch-name>
   git pull --ff-only origin <branch-name>
   ```

3. Keep the branch's tracked `docker-compose.yml` unmodified. Copy
   `docker-compose.override.yml.example` to `docker-compose.override.yml`. The
   override builds both AssistantMD and the advanced shell from this checkout;
   set any required UID/GID choices, external networks, resource limits, and
   deliberate bind mounts there. Put vault and system host paths in `.env`.

4. Preserve the existing `ASSISTANTMD_SECRETS_KEY` in `.env`. Configure the
   authentication and public URL exactly as described under
   [Access from another device](../setup/installation.md#access-from-another-device). Add
   both `COMPOSE_PROFILES=advanced` and
   `ASSISTANTMD_EXECUTION_MODE=advanced` when testing advanced mode.

5. If a reverse proxy runs in another Compose project, attach `assistant` to
   both `assistantmd_advanced_shell` and the proxy's external network. Keep
   `advanced-shell` only on `assistantmd_advanced_shell`.

6. Validate the merged configuration before building:

   ```bash
   docker compose config --quiet
   docker compose config --services
   docker compose config --networks
   ```

   With advanced mode enabled, both `assistant` and `advanced-shell` must appear.
   The network list must include `assistantmd_advanced_shell` and any external
   proxy network you configured. Do not copy or share unfiltered `docker compose
   config` output because it can contain values loaded from `.env`.

7. Build and deploy. Build both images when advanced mode is enabled:

   ```bash
   docker compose build --pull --no-cache assistant advanced-shell
   docker compose up -d --force-recreate
   docker compose ps
   ```

   For restricted mode, building only `assistant` is sufficient. Do not use
   `docker compose down -v`; the `-v` option deletes advanced-shell identities,
   installed files, and workspace data.

8. Inspect startup logs and **System → Infrastructure**. In advanced mode:

   ```bash
   docker compose logs --tail=100 assistant advanced-shell
   ```

   Confirm the intended authentication mode. Advanced mode is usable when its
   execution mode is `advanced` and the advanced shell reports `ready`.
   Restricted deployments can inspect `assistant` alone.

For later branch updates, repeat the fetch/pull, build, and `up -d
--force-recreate` steps. This workflow intentionally stops short of unusual
orchestrators, remote Docker builders, and custom network-policy systems.

## Use the Python environment

Activation is optional. Prefer `uv run` for ad hoc commands because it does not
depend on terminal state:

```bash
uv run python -V
uv run python -c "import logfire; print(logfire.__version__)"
```

To activate the environment:

```bash
source .venv/bin/activate
python -V
```

`python` should resolve to `.venv/bin/python` and report Python 3.13.

## Development commands

Run the production Python quality gate:

```bash
scripts/dev check
```

Run one validation scenario:

```bash
scripts/dev scenario integration/core/api_error_resilience
```

Maintainers own the full scenario-validation suite. Contributors and coding
agents should run focused scenarios for the behavior they change. Validation
runs end with a failure-focused summary and write durable indexes to
`validation/runs/reports/latest.md` and `validation/runs/reports/latest.json`.
Pass `--show-passed` to the validation CLI when individual successful scenarios
are useful:

```bash
uv run python validation/run_validation.py run integration/core --show-passed
```

Show all commands:

```bash
scripts/dev help
```

## Diagnose setup problems

```bash
scripts/dev doctor
```

The doctor checks required commands, Node.js 22, the venv interpreter, Python
3.13, the Logfire import, canonical public URL validity, and optional Chromium
availability without changing the environment.

If activation succeeds but `python` is missing, the venv probably references an
interpreter removed with an earlier host or container. Repair it with:

```bash
scripts/dev setup
```

If Playwright reports a missing browser or missing native libraries, run:

```bash
scripts/dev setup --browser
```

The project Python environment and `node_modules` are checkout-local. UV's
managed interpreter and package cache, npm's cache, and Playwright's browser
cache may be host-level optimizations. Replacing a host or coding container can
remove those caches; rerunning setup restores the checkout.
