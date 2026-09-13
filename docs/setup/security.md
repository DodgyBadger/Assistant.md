# Security Considerations

## Philosophy

Assistant.md is designed as a **single-user application** running on your local machine or private server.

## Application exposure

Assistant.md requires an explicit ingress-authentication mode:

- `loopback` is for direct host-process runs, primarily development through `scripts/dev run`. It admits only an actual `127.0.0.1` or `::1` socket peer and provides no login. It is not applicable to the standard Docker Compose installation.
- `trusted_proxy` requires a secret assertion injected by an authenticating reverse proxy. It reuses the proxy's human login rather than adding another.
- `owner_token` provides a single-owner token exchange and signed HttpOnly browser session. Use HTTPS for every non-loopback deployment.
- `disabled` intentionally leaves the complete UI and API open to every routable peer, including advanced-shell containers. It is intended for recovery and deliberate testing and is not recommended for network-accessible use.

The selected mode authenticates requests as the current single-user `local-user` principal. Assistant.md does not provide user registration, password recovery, roles, or transport encryption.

For `trusted_proxy`, the proxy must remove any client-supplied assertion header before inserting its own value. Keep the shared assertion outside browser responses and outside the advanced-shell container. Configure a trusted immediate proxy network as defense in depth where the deployment has stable addressing. Only the authenticating proxy should be able to reach the Assistant.md upstream. The [Installation Guide](installation.md#access-from-another-device) contains the copyable proxy and Docker-network configuration.

For `owner_token`, store the credential in the deployment's protected `.env` file and do not reuse the installation encryption key. The browser session lasts up to 12 hours. Logout clears browser cookies but does not revoke a copied stateless session; rotate the owner token to invalidate all outstanding sessions.

Application middleware rejects aggregate request headers over 64 KiB, but the HTTP server receives headers before application code runs. Reverse proxies must apply an equal or smaller header limit, authentication-failure rate limits, TLS, and appropriate request-body limits at ingress.

For reverse-proxy deployments, configure `ASSISTANTMD_PUBLIC_URL` with the externally visible HTTPS origin. This lets Assistant.md construct exact OAuth callbacks without trusting request host or forwarded headers. It does not configure DNS, TLS, authentication, or proxy routing; the proxy must still route the callback path and satisfy the configured authentication mode.

Keep these constraints in mind before putting the application on a public interface.

## Advanced shell

Advanced execution mode gives interactive chat a general, noninteractive shell inside a separate persistent container. Commands may install and execute software, read and write the advanced shell's home and workspace, and use outbound network access unless the operator imposes additional network policy. Treat enabling this mode as granting the model authority over everything made available to that container.

The environment is intentionally constrained: commands run as a non-root user, the base filesystem is read-only, tool calls have bounded execution, and the container is not a systemd host or supported cron/service platform. Persistent home and workspace volumes preserve files, not processes. Services that must run continuously or restart autonomously should use a separately reviewed Compose service with their own explicit privileges, mounts, network policy, and lifecycle.

Docker is the primary isolation boundary. The supplied service runs with a read-only container filesystem, dedicated writable volumes, a PID and memory limit, dropped capabilities except those required by OpenSSH, and `no-new-privileges`. Preserve those controls. Never mount the Docker socket, Assistant.md's `system/` directory, a host home or root directory, or other administrative interfaces into the advanced shell.

Assistant.md and the advanced shell generate separate SSH identities and exchange only public keys through one-way Docker volumes. Assistant.md pins the resulting host identity, and the advanced shell accepts only the enrolled Assistant.md client. This authenticates the fixed control channel; it does not make commands, packages, MCP servers, or other software executed inside the advanced shell trustworthy.

Assistant.md's encrypted credentials, installation key, owner token, and trusted proxy assertion are intentionally absent from the advanced shell. Do not mount or copy them into it. In `disabled` authentication mode, every routable peer can use the complete Assistant.md API; this includes the advanced-shell container because it shares the Compose network. The authenticated modes prevent that access unless their credential is separately disclosed.

Every bind mount expands the shell's authority:

- a read-only mount discloses all readable content beneath it;
- a writable mount also permits creation, modification, and deletion; and
- mount permissions are a deployment boundary, not an instruction the model can enforce for itself.

Credentials may be stored deliberately in the advanced shell for software that needs them, but they are then readable and usable by chat through `shell`. Prefer Assistant.md-managed encrypted credentials and normal MCP connections when the server supports them. If a credential must live in the advanced shell, scope it narrowly and accept that it belongs to the agent-accessible environment. Do not paste credentials into chat or command arguments: shell tool calls and results are part of chat history even though the separate activity log excludes command text, stdin, stdout, and stderr.

Registering a stdio provider as an MCP connection keeps its disclosed tools inside Assistant.md's tool allowlist and deferred tool-search path. Chat can instead start or communicate with a process directly through `shell`, bypassing those MCP discovery and allowlist controls. That escape hatch is inherent to general shell access; use it deliberately and prefer a registered connection for ongoing use.

The current single-user deployment exposes one shared advanced shell only to the initial `local-user` tenancy. It does not provide per-principal Unix accounts, filesystems, processes, credentials, or resource isolation. A future multiuser deployment must add an explicit isolation strategy before granting advanced-shell access to mutually untrusted principals.

## Encrypted credentials

API keys, OpenAI OAuth state, connection credentials, and provider OAuth state are encrypted at rest in `system/access.db`. Protect and back up the installation key in `.env` separately; restoring stored credentials requires both the database and its key, and losing the key requires re-entering credentials.

Connection names, immutable slugs, client IDs, default selection, and capability preferences are non-secret metadata. Secret values are write-only through the UI and API and must not be copied into chat, logs, or the advanced-shell environment.

## Gmail and Google connections

Google OAuth client secrets, access tokens, refresh tokens, connected account identity, and pending authorization state are principal-owned, encrypted, and scoped to a named Google connection. Gmail tools are absent unless the current user has a connection with the required scope.

Treat every email header, body, attachment descriptor, and downloaded file as untrusted external data. Bounded PDF attachments can be written to a caller-selected vault path, but attachment bytes are never exposed to chat or logs. PDF format checks do not establish that a downloaded file is safe.

Draft creation is a separate per-connection opt-in and requires Google's compose scope. Although that scope also permits sending, Assistant.md exposes only recipient-free draft creation and does not retry a request whose outcome is uncertain. Draft content remains in the chat/session record but is excluded from operational logs. Disabling the feature blocks Assistant.md draft creation but does not revoke a compose scope already granted by Google; disconnect and reconnect the account, or revoke Assistant.md in Google account settings, to remove provider-side authority.

See the [Connections guide](../use/connections.md#connect-gmail) for setup and the [`gmail` tool reference](../tools/gmail.md) for the operation and limit contract.

## MCP servers

MCP servers are trusted tool providers, not passive content sources. Enabling a connection permits the primary chat model to call every allowed server tool; Assistant.md cannot infer whether an MCP tool only reads data or causes external side effects. Use exact allowlists and connect only servers you trust. Server tool descriptions, results, and other returned content remain untrusted data.

MCP definitions and OAuth or static credentials are principal-owned. Credentials and OAuth state are encrypted in `system/access.db`, alongside sanitized connection metadata in separate domain tables. Remote endpoints require HTTPS. Private-network HTTP requires explicit acknowledgement on each connection and never permits public plaintext endpoints.

Stdio MCP providers run inside the advanced shell and inherit that environment's filesystem, process, credential, and network authority. The advanced-shell boundary above applies in addition to normal MCP tool allowlists and limits.

See the [Connections guide](../use/connections.md#connect-an-mcp-server) for remote and stdio MCP setup.

## Vault file uploads

Vault Explorer uploads intentionally accept arbitrary file content because vaults may contain PDFs, images, office documents, and other user-owned artifacts. The upload boundary:

- requires an existing configured vault and a safe vault-relative destination;
- rejects absolute paths, `.`/`..` components, control characters, overlong paths, and paths that resolve through symlinks outside the selected vault;
- treats the multipart filename as display metadata only and uses the validated API path as the destination;
- accepts exactly one multipart file per request;
- enforces `vault_upload_max_mb_per_file` and never overwrites an existing destination; and
- writes through vault mutation history.

Uploaded content is not malware-scanned. Assistant.md does not execute uploaded files or render unknown binary types inline, but explicitly importing an untrusted PDF or image hands that content to the configured ingestion parser. Keep parser dependencies current and only import documents you are willing to process.

The application-level size check is not a substitute for an edge request-body limit. In particular, a chunked multipart request may be spooled by the HTTP stack before the application can reject its file bytes, and repeated within-limit uploads can consume vault storage. Remote deployments should set request-size, rate, and storage limits at the authenticated reverse proxy.

## Prompt injection

### The risk

Web pages, email, imported documents, MCP results, and other external content can contain text designed to override the model's instructions. Treat retrieved content as data even when it comes from an authenticated or familiar service.

Potential impact includes incorrect or misleading output and unintended tool calls. When `file_write` or another mutating capability is available, a successfully manipulated model may also change or delete data within that tool's authority.

### Mitigations

- Assistant.md's web and connection-backed tools tell models to treat returned content as untrusted. This reduces risk but cannot guarantee that a model will ignore every hostile instruction.
- Retrieval-provider filtering, including filtering offered by Tavily, is defense in depth. It does not make returned content trusted or replace Assistant.md's own boundaries.
- The `browser` tool blocks downloads, local/private network targets, unsafe redirects and subrequests, and non-read HTTP methods. Browser state is isolated per call, and extraction focuses on the main content region when possible.
- `web_extract` and URL ingestion validate public-network targets at the initial URL and every redirect. Provider strategies are explicit and do not silently fall back to another network path.
- Built-in web and browser tools expose narrow retrieval behavior rather than arbitrary outbound actions. Residual risk remains wherever the application is intentionally configured to communicate with an external provider, site, or tool server.

These controls reduce the blast radius; they do not make retrieved content safe or trustworthy.

### Best practices

- Review outputs from workflows and context templates that process external content.
- Disable `file_write` app-wide when a deployment should not permit model-driven vault mutations.
- Use inline edit mode to inspect interactive chat mutations before they execute.
- Be especially cautious when combining mutating tools with web, email, imported documents, or MCP content.
- Prefer the least powerful web tool that can do the job: search for discovery, extract for a known page URL, and browser only when simpler retrieval is insufficient.
- Start browser work with one extraction pass before attempting narrower selectors or follow-up actions.
- Test prompt-injection-sensitive workflows before trusting them with mutating capabilities.
- Keep backups of important vault data.
