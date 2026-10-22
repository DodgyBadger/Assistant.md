# Security Considerations

Assistant.md is a **single-user application** for a local machine or private server. Its security depends on how you expose the application and what files, credentials, and tools you make available to it.

## Application exposure

Choose an explicit authentication mode:

- `loopback` admits only an actual `127.0.0.1` or `::1` socket peer and provides no login. Use it for direct host-process runs such as `scripts/dev run`, not standard Docker Compose installations.
- `trusted_proxy` relies on an authenticating reverse proxy and a secret assertion header, reusing the proxy's login.
- `owner_token` provides a single-owner login and a signed HttpOnly browser session.
- `disabled` leaves the complete UI and API open to every routable peer, including advanced-shell containers. Use it only where that access is deliberate; never expose an unauthenticated port to a network.

Assistant.md provides no TLS, user registration, password recovery, or roles. Use HTTPS for remote access. The [Installation Guide](installation.md#access-from-another-device) provides authentication and proxy configuration examples.

For `trusted_proxy`, remove any client-supplied assertion header before inserting the proxy's value. Keep the assertion out of browser responses and the advanced shell, and allow only the authenticating proxy to reach the upstream. Restrict the immediate proxy network where stable addressing permits it.

For `owner_token`, protect the credential in `.env` and use a different secret from the installation encryption key. Browser sessions last up to 12 hours. Logout clears cookies but does not revoke a copied session; rotate the owner token to invalidate outstanding sessions.

Configure `ASSISTANTMD_PUBLIC_URL` with the externally visible HTTPS origin for exact OAuth callbacks. This setting does not establish TLS or proxy routing. At ingress, enforce authentication-failure rate limits, request-body limits, and an aggregate header limit no larger than 64 KiB. Application checks run after the HTTP server has received the request.

## Advanced shell

Enabling advanced mode gives interactive chat authority over the files, software, credentials, and network access available inside its separate container. Commands can install and execute software. The environment is shared within the single-user deployment; it does not isolate mutually untrusted users.

Docker is the primary isolation boundary. Preserve the supplied non-root execution, read-only filesystem, resource limits, and privilege restrictions. Never mount the Docker socket, Assistant.md's `system/` directory, a host home or root directory, or other administrative interfaces into the shell. See [advanced-mode setup](installation.md#enable-advanced-mode) for mounts and persistence.

Every bind mount expands the agent's authority:

- a read-only mount exposes all readable content beneath it;
- a writable mount also permits creation, modification, and deletion; and
- instructions to the model cannot replace filesystem permissions.

The authenticated control channel connects Assistant.md to its configured shell; it does not make executed commands, packages, or MCP providers trustworthy. Keep the installation key, owner token, proxy assertion, and Assistant.md-managed credentials outside the shell. In `disabled` authentication mode, the shell can reach the unprotected API through the Compose network.

Any credentials deliberately stored in the shell are readable and usable by chat. Scope them narrowly and prefer managed connections where possible. Do not paste credentials into chat or command arguments: shell calls and results remain in chat history even though System Activity excludes command text and output.

A registered stdio MCP connection limits which tools are disclosed to chat. General shell access can bypass that allowlist by communicating with the process directly. Prefer registered connections for ongoing use, while recognizing that their allowlists do not restrict the shell itself.

## Encrypted credentials

API keys and OAuth/connection credentials are encrypted at rest in `system/access.db`. Back up the installation key in `.env` separately from `system/`: restoring credentials requires both the database and key. Losing the key requires re-entering credentials.

Connection settings such as names and capability preferences are non-secret metadata. Secret fields are write-only through the UI and API. Do not copy their values into chat, logs, or the advanced shell.

## Gmail and Google connections

Gmail tools require an authorized connection with the appropriate permissions. Treat email, attachment descriptions, and downloaded files as untrusted external data. PDF format checks do not establish that a downloaded attachment is safe.

Draft creation is a separate opt-in requiring Google's compose scope. Although that scope also permits sending, Assistant.md exposes only recipient-free, unsent drafts and does not automatically retry draft creation when its outcome is uncertain. Draft content remains in chat history but is excluded from operational logs.

Disabling drafts blocks creation in Assistant.md but does not revoke Google's compose grant. Disconnect and reconnect with drafts disabled, or revoke Assistant.md in Google account settings, to remove that permission. See the [Connections guide](../use/connections.md#connect-gmail) for setup and the [`gmail` reference](../tools/gmail.md) for supported operations.

## MCP servers

Enabling an MCP connection lets primary chat call its allowed tools, which may read private data or cause external changes. Connect only trusted servers and use exact tool allowlists. Server descriptions and results still count as untrusted content.

Remote endpoints require HTTPS. Unencrypted private-network HTTP requires explicit acknowledgement and leaves traffic and credentials exposed; public plaintext endpoints are blocked. Stdio providers inherit the advanced shell's filesystem, process, credential, and network access.

See the [Connections guide](../use/connections.md#connect-an-external-mcp-server) for remote and stdio setup.

## Vault file uploads

Uploads accept arbitrary file types but require a safe destination inside a configured vault, enforce `vault_upload_max_mb_per_file`, never overwrite an existing file, and record changes in vault mutation history.

Uploaded files are not malware-scanned or executed automatically. Importing a PDF or image passes it to the configured parser, so keep parser dependencies current and process only content you are willing to handle.

Set request-size, rate, and storage limits at the authenticated reverse proxy. Uploads may consume HTTP-stack resources before application checks run, and repeated uploads within the per-file limit can still exhaust storage.

## Prompt injection

Web pages, email, imported documents, and MCP results can contain instructions intended to manipulate the model. Treat retrieved content as data even when its source is familiar or authenticated. Successful manipulation can produce misleading answers or unintended tool calls, including changes or deletion within a mutating tool's authority.

Built-in retrieval tools restrict network targets and actions, and their instructions identify returned content as untrusted. These controls and provider filtering reduce risk but cannot guarantee that a model ignores hostile instructions. Connected services and the advanced shell may grant broader authority.

- Review outputs and test workflows that process external content before giving them mutating capabilities.
- Disable `file_write` app-wide when model-driven vault changes should be prohibited.
- Use inline edit mode to inspect interactive chat file mutations. This does not add review to unattended workflows or delegate runs.
- Grant only the access needed for the task and keep backups of important vault data.
