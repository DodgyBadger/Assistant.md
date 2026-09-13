# Connections

Connections let Assistant.md use a Google account or tools provided by an MCP server. Open **System → Connections** to add and manage them.

Only add accounts and servers you trust. A connected account can expose private data, and an MCP tool may take actions outside Assistant.md. Review the [security guidance](../setup/security.md#gmail-and-google-connections) before connecting sensitive services.

## Connect Gmail

Assistant.md needs an OAuth client from a Google Cloud project before it can ask for access to Gmail.

1. Set `ASSISTANTMD_PUBLIC_URL` in `.env` to the address where you open Assistant.md, then restart it. This gives Assistant.md a stable callback address for Google.
2. In **System → Connections**, choose **Add Google** and copy the **Authorized redirect URI** shown on the new connection.
3. In [Google Cloud](https://console.cloud.google.com/), create or select a project, enable the Gmail API, and configure the Google Auth consent screen. If the app is in testing, add the Google account you will connect as a test user.
4. Create an OAuth client for a **Web application**. Add the URI copied from Assistant.md as an authorized redirect URI.
5. Copy the client ID and client secret into Assistant.md. Give the connection a recognizable name, choose whether it is your default Google connection, and save it.
6. Choose **Authorize Google**, sign in to the intended Google account, and approve the requested access.

Assistant.md normally detects the completed authorization. If the browser cannot reach the callback, copy the full redirected URL from its address bar, paste it into the connection card, and choose **Finish authorization**.

The connection is ready when its card reports that Gmail tools are available. For help with the Google-side setup, see Google's [Gmail API setup guidance](https://developers.google.com/workspace/gmail/api/auth/web-server). Google authorizations for test users expire after seven days while the consent screen remains in **Testing**, so those users will need to authorize again.

### Choose Gmail capabilities

Reading messages is enabled when you authorize the connection. Two more capabilities are deliberately off by default:

- **Allow attachment downloads** permits bounded PDF attachments to be saved into a vault path. Set the size limit appropriate for your installation.
- **Allow draft creation** permits Assistant.md to create unsent, recipient-free drafts. Enabling it requires you to authorize again so Google can grant the additional permission.

Disabling draft creation later stops Assistant.md from creating drafts, but it does not revoke permission already granted by Google. To remove that permission at Google, disconnect and reconnect the account with drafts disabled, or revoke Assistant.md from the Google account.

You can add more than one Google account. Mark one as the default; Assistant.md uses it when a request does not name a connection. Use clear connection names so you can ask for a specific account when needed.

See the [`gmail` reference](../tools/gmail.md) for supported operations and limits.

## Connect an external MCP server

An MCP connection makes a server's tools available to the primary chat. Obtain the server URL, transport, authentication instructions, and recommended tool list from the server provider.

1. In **System → Connections**, choose **Add MCP**.
2. Enter a clear display name and the server URL.
3. Select the transport and authentication method specified by the provider.
4. In **Allowed tools**, enter the exact tool names Assistant.md may use, separated by commas. Leaving this blank trusts every tool the server exposes.
5. Save the connection. If it uses OAuth, authorize it as described below. Then use **Test** on its card, review the discovered tools, and resolve any error before relying on it.
6. Leave the connection enabled only when you want its allowed tools available to chat.

Use **Streamable HTTP** unless the provider specifically requires **SSE**. Remote servers should use HTTPS. Assistant.md blocks public, unencrypted HTTP. For a trusted server on your private network, you can explicitly enable **Allow HTTP on a private network**, but its traffic and credentials will not be encrypted.

### Choose MCP authentication

- **None** is appropriate only when the server does not require credentials.
- **Bearer token** sends the stored credential as a bearer token.
- **Custom header** sends the stored credential using the header name supplied by the server provider.
- **OAuth** opens the server's sign-in and consent flow. Leave the client ID and secret blank when the server supports automatic client registration; otherwise enter the client details supplied or registered for Assistant.md. Add scopes only when the provider tells you to request them.

Save authentication changes before choosing **Authorize** or **Test**. For OAuth, register the **Authorized redirect URI** shown on the connection card if the provider requires a pre-registered client. If the callback cannot reach Assistant.md, paste the full redirected URL into the card and choose **Finish from redirected URL**.

Credentials are write-only after saving. Entering a new value replaces the stored credential; leaving a secret field blank preserves it. Disconnecting OAuth removes the authorization while preserving the connection settings.

### Connect a local stdio server

Stdio MCP servers run inside Assistant.md's optional advanced shell. Complete [advanced-mode setup](../setup/installation.md#enable-advanced-mode) first. Then ask chat to follow the bundled **Advanced Shell MCP Setup** skill for the server you want to install. Review the generated YAML or JSON, paste it into the import field under **Add MCP**, and choose **Import**.

Importing only fills the form. Check the executable, arguments, working directory, roots, non-secret environment values, and allowed tools before you add the connection. Assistant.md tests a new stdio connection before enabling it.

Stdio servers inherit the advanced shell's filesystem, process, and network access. Treat them with the same care as software you run directly in that environment. See the [`shell` reference](../tools/shell.md) for that boundary.

## Manage connections safely

- **Disable** an MCP connection to keep its settings without exposing its tools.
- **Disconnect** an OAuth account to remove its saved authorization while keeping the connection configuration.
- **Delete** a connection to remove its configuration and stored credentials.
- Re-test MCP connections after changing their URL, transport, authentication, or allowed tools.

You can rename a connection without breaking saved references to it.
