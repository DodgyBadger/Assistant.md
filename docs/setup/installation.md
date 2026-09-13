# Installation

The recommended installation uses Docker Compose and keeps deployment settings in one `.env` file.

Assistant.md is the product and repository name. Some identifiers retain the former styling `AssistantMD`, including the vault system folder, environment variables, image names, and container names. Commands and paths in this guide should be used exactly as written.

## 1. Install the prerequisites

You need:

- [Docker Engine](https://docs.docker.com/engine/install/) on Linux or [Docker Desktop](https://www.docker.com/products/docker-desktop/) on Windows or macOS; and
- access to a cloud or local language model.

Back up an existing vault before mounting it, or begin with a test vault.

## 2. Create the deployment folder

Clone Assistant.md and create the two persistent folders:

```bash
git clone https://github.com/DodgyBadger/Assistant.md.git
cd Assistant.md
mkdir -p data system
cp .env.example .env
```

This creates the normal deployment layout:

```text
Assistant.md/
├── data/
├── system/
├── .env
└── docker-compose.yml
```

Note: `data/` provides the default local vault location and is useful for testing. It can remain empty when `ASSISTANTMD_DATA_PATH` points to a vault folder elsewhere.

`docker-compose.yml` is maintained by the repository. Do not edit it; future `git pull` operations can then deliver required service, network, and volume updates. Put ordinary deployment choices in `.env`. Use optional `docker-compose.override.yml` for structural customizations described later in this guide.

## 3. Configure `.env`

Generate the required encryption key:

```bash
openssl rand -base64 32 | tr '+/' '-_' | tr -d '='
```

Open `.env`, put the generated key in `ASSISTANTMD_SECRETS_KEY`, and review these initial settings:

```dotenv
ASSISTANTMD_SECRETS_KEY=PASTE_GENERATED_KEY_HERE
ASSISTANTMD_DATA_PATH=./data
ASSISTANTMD_SYSTEM_PATH=./system
TZ=UTC
ASSISTANTMD_AUTH_MODE=disabled
```

Leave `ASSISTANTMD_DATA_PATH=./data` unchanged to start with the empty local vault location created during setup. To use existing vaults, replace `./data` with their absolute parent folder, such as `/absolute/path/to/your/vaults`. Each direct subfolder becomes an Assistant.md vault. The examples later in this guide show single-vault and multi-vault layouts.

The supplied Compose file publishes Assistant.md only on the host's loopback address. This is the simplest setup when you will open Assistant.md on the same computer. `disabled` means there is no application login, so do not expose that port to a network.

If you will access Assistant.md through a TLS reverse proxy, choose `trusted_proxy` or `owner_token` instead. Follow [Access from another device](#access-from-another-device) before starting it.

Keep `.env` safe and back it up separately from `system/`. You need both the encryption key and `system/access.db` to restore stored credentials and connections. On Linux, restrict it with `chmod 600 .env`.

## 4. Start Assistant.md

```bash
docker compose up -d
```

## 5. Open Assistant.md

Open <http://127.0.0.1:8000/>. If it does not load, inspect the container log:

```bash
docker logs assistantMD
```

## 6. Configure a model provider

Open **System → Model Providers** and configure at least one provider.

For OpenAI, either setup path is valid:

- **API key:** add `OPENAI_API_KEY` under **System → Secrets**. The built-in OpenAI provider uses that secret by default.
- **OAuth:** enable `openai_oauth_enabled` under **System → Application Settings**. Return to **Model Providers**, edit OpenAI, select **OAuth**, save, and choose **Connect** or **Device Code** in its OAuth panel.

OpenAI OAuth remains labeled experimental because OpenAI does not officially support this Assistant.md integration and may change or restrict the flow. It is nevertheless a complete and valid way to enable the OpenAI provider during first setup. API-key authentication remains available if OAuth is unsuitable.

For another provider, add its requested secret or endpoint from the same System page. Once one provider is ready, Assistant.md is ready to use.

Assistant.md adds an `AssistantMD/` folder to each mounted vault for skills, workflows, imported documents, and exported chats. See [Getting the Most from Assistant.md](../use/getting-the-most.md) when you are ready to customize how it works.

## Optional setup

### Access from another device

Assistant.md does not provide TLS. Remote access should use HTTPS and one of these authentication options:

- `trusted_proxy` when an existing reverse proxy already authenticates users;
- `owner_token` when the reverse proxy provides TLS but not authentication.

Generate a second secret for authentication. Do not reuse `ASSISTANTMD_SECRETS_KEY`:

```bash
openssl rand -hex 32
```

#### Built-in owner login

Add the following to `.env`:

```dotenv
ASSISTANTMD_AUTH_MODE=owner_token
ASSISTANTMD_AUTH_SECRET=PASTE_A_DIFFERENT_RANDOM_SECRET_HERE
ASSISTANTMD_PUBLIC_URL=https://assistant.example.com
```

Configure your TLS reverse proxy to forward requests to Assistant.md, then run `docker compose up -d`. The first browser visit shows Assistant.md's owner login; enter `ASSISTANTMD_AUTH_SECRET` there.

For example, a Caddy process running on the Docker host needs only:

```caddyfile
assistant.example.com {
    reverse_proxy 127.0.0.1:8000
}
```

#### Existing authenticating proxy

Add the following to `.env`:

```dotenv
ASSISTANTMD_AUTH_MODE=trusted_proxy
ASSISTANTMD_AUTH_SECRET=PASTE_A_DIFFERENT_RANDOM_SECRET_HERE
ASSISTANTMD_PUBLIC_URL=https://assistant.example.com
```

Give the proxy process the same `ASSISTANTMD_AUTH_SECRET` through its own secure environment configuration. Do not give the proxy Assistant.md's complete `.env`. After the proxy's authentication handler, replace the assertion header before forwarding the request. For Caddy:

```caddyfile
reverse_proxy assistant:8000 {
    header_up X-AssistantMD-Proxy-Assertion {$ASSISTANTMD_AUTH_SECRET}
}
```

This upstream assumes Caddy shares a Docker network with Assistant.md. Use `127.0.0.1:8000` instead when Caddy runs directly on the Docker host.

Run `docker compose up -d`. Assistant.md now accepts requests carrying the proxy-only assertion and does not show a second login.

#### Proxy in another Compose project

For either authentication mode, a containerized proxy must share a network with Assistant.md. Create `docker-compose.override.yml` with the following structure, changing `caddy_default` to the proxy network's name. Preserve the advanced-shell network:

```yaml
services:
  assistant:
    networks:
      - assistantmd_advanced_shell
      - caddy_default

networks:
  assistantmd_advanced_shell:
  caddy_default:
    external: true
```

Run `docker compose config` after changing the override and verify that the rendered `assistant` service retains `assistantmd_advanced_shell`, joins the proxy network, and has the intended published-port configuration. Then run `docker compose up -d`. Use `assistant:8000` as the proxy upstream.

For either option, use the exact HTTPS origin shown in the browser for `ASSISTANTMD_PUBLIC_URL`. See [Security Considerations](security.md#application-exposure) for the mode risks, proxy hardening, and deliberately unprotected `disabled` mode.

The `loopback` authentication mode is for direct development runs such as `scripts/dev run`, where Assistant.md is a host process rather than a Docker Compose service. It is not applicable to the standard Docker installation.

### Enable advanced mode

Advanced mode gives Assistant.md access to a Bash command line in a separate, non-root Linux container, greatly expanding what it can do. Files under `/home/advanced-shell` and `/workspace` survive ordinary container restarts and upgrades. Processes and `/tmp` do not, and the container does not provide systemd or a supported cron/service supervisor.

The advanced shell cannot see your vaults unless you explicitly mount vault content into its container. For security, mount one narrow read-write exchange folder rather than an entire writable vault. You can precreate `AssistantMD/shell-exchange` inside a vault, or select another existing folder in that vault, then bind only that exact host path to `/exchange` through `docker-compose.override.yml`. Assistant.md sees the same files through its normal vault mount, so neither side needs a separate copy or Docker-managed exchange volume.

Docker creates a missing bind-mount source as `root`, which can make it unusable by both application users. Create the exchange folder before starting the advanced profile and retain `bind.create_host_path: false` from the override example. On a first installation, the vault's `AssistantMD/` folder does not yet exist. Either select an existing vault folder for the initial exchange, or start once in restricted mode, let Assistant.md create its vault folders, create `AssistantMD/shell-exchange`, and then enable advanced mode.

Examples are available in `docker-compose.override.yml.example`. Prefer read-only mounts for any additional vault content. Never mount Assistant.md's `system/` folder, the Docker socket, a host home folder, or the host root. See [Security Considerations](security.md#advanced-shell) before adding access or credentials.

After preparing any exchange mount, add both settings to `.env`:

```dotenv
COMPOSE_PROFILES=advanced
ASSISTANTMD_EXECUTION_MODE=advanced
```

The first starts the optional container; the second explicitly authorizes Assistant.md to expose the capability. Before starting, run `docker compose config` and inspect the rendered `advanced-shell` volumes and networks. Confirm that every bind-mount source expanded to the intended absolute host path and every target is unique. Then run `docker compose up -d` and confirm **System → Infrastructure** reports the advanced shell as `ready`.

Stdio MCP servers installed there are launched when Assistant.md needs them. Ask chat to follow the bundled **Advanced Shell MCP Setup** skill, then review and paste its generated YAML or JSON into **System → Connections**.

Contributor setup for running Assistant.md and its advanced shell from a checkout belongs in the [Development Guide](../development/dev-setup.md).

### Configure connections

Open **System → Connections** to add Gmail accounts and MCP servers. Follow the [Connections guide](../use/connections.md) for the Google Cloud setup, OAuth callbacks, MCP authentication, connection testing, and capability choices. For stdio MCP providers, complete [advanced-mode setup](#enable-advanced-mode) first. Review [Security Considerations](security.md) before providing credentials to a server or granting tool access.

### Enable optional integrations

- Web search works without another key. Add a Tavily key under **Secrets** for Tavily-backed search, extraction, and crawling.
- The published image includes the browser runtime. Hosts should make at least 2 GB available to Assistant.md when the browser tool is enabled.
- Logfire is optional. Add its key under **Secrets** and enable Logfire in **Application Settings** if you want remote diagnostics.

## File permissions on Linux

The published image runs as UID 1000. The host vault and `system/` folders must be writable by that user. If logs report `permission denied`, check the folder ownership first.

If your host user has another UID, clone the repository, copy `docker-compose.override.yml.example` to `docker-compose.override.yml`, set its `USER_ID`, `GROUP_ID`, and `user` values, then run. IDs must be non-root decimal integers. Changing runtime IDs does not repair ownership of existing bind-mounted files or named-volume content; repair ownership deliberately before restarting with the new identity.

```bash
docker compose up -d --build
```

Docker Desktop normally handles this mapping automatically on Windows and macOS.

## Vault Path Examples

```
/home/user/MyVaults/
├── Personal/
```

`.env` reads: `ASSISTANTMD_DATA_PATH=/home/user/MyVaults`. Assistant.md will see one vault called `Personal`.

```
/home/user/MyVaults/
├── Personal/
├── Work/
└── Family/

```

`.env` reads: `ASSISTANTMD_DATA_PATH=/home/user/MyVaults`. Assistant.md will see three vaults called `Personal`, `Work` and `Family`

## Platform notes

- On Windows, WSL paths under `/mnt` are usually the simplest vault mounts.
- Quote a volume entry when its host path contains spaces.
- A local model server must listen on an address reachable from the Assistant.md container. Use its LAN address when it runs on the host, or its service name when both applications share a Docker network.
