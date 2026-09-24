# Assistant.md

Assistant.md is a self-hosted AI agent harness for non-coding knowledge work. It is built with a focus on:

- **Safe automation:** no direct host access and a limited blast radius if something goes wrong
- **Rapid onboarding:** useful out of the box, with plenty of room to customize and grow
- **Observability:** behaviour is explicit and important activity is logged
- **Data ownership:** everything remains a useful, portable file with or without Assistant.md

Mount one or more Markdown vaults when you install Assistant.md, and they become available to the chat agent and automated workflows. At the start of each chat session, choose any folder within a vault as the workspace, giving the agent immediate project context.

> [!IMPORTANT]
>
> **v0.8.0 is a major agentic upgrade.** Assistant.md can now connect to remote MCP services, run local MCP providers and commands in an optional advanced shell environment, work with Gmail, and securely manage the credentials those capabilities require.

## Features

- **Agentic work sessions:** Run long-lived, tool-heavy work that continues after you disconnect, with context protection, subagent delegation, goal tracking, and automatic session compaction.
- **Project-aware workspaces:** Scope a chat to any vault folder and provide project-specific guidance through familiar Markdown files such as `README.md` and `playbook.md`.
- **Vault explorer:** Browse, preview, edit, upload, import, move, organize, and search files in your vault.
- **MCP tools:** Connect to remote Streamable HTTP or SSE servers with lazy tool discovery that keeps the context window lean.
- **Gmail connections:** Search and read mail, import PDF attachments to Markdown, and create unsent drafts across one or more connected accounts.
- **Advanced shell:** Give the chat agent an optional sandboxed Linux shell for advanced tools, including local stdio MCP providers, without exposing the application host.
- **Research and ingestion:** Search, extract, and crawl web content, then turn public pages and vault PDFs into Markdown.
- **Composable automation:** Build sandboxed Python workflows and context assembly scripts for deep customization and repeatable work.
- **Reviewable and recoverable changes:** Inspect proposed file edits before applying them, restore revisions, or roll back changes.
- **Operational visibility:** Review workflow history, running work, tool details, and searchable System Activity.
- **Flexible model support:** Use supported cloud or local models, including multimodal models and OpenAI OAuth.
- **Explicit security controls:** Encrypt credentials at rest, authorize connections independently, and choose from several endpoint security modes.
- **Focused interface:** Work in a clean, minimal UI with focus and dark modes.

Assistant.md is useful with its default setup, but its behavior is deliberately composable. Edit Markdown guidance for simple customization, or use sandboxed Python when you need custom context assembly and repeatable workflows. See [Getting the Most from Assistant.md](docs/use/getting-the-most.md) to get started.

## Documentation

### Using Assistant.md

- **[Installation Guide](docs/setup/installation.md)**
- **[Connections](docs/use/connections.md)** — connect Google accounts and remote or local MCP servers
- **[Getting the Most from Assistant.md](docs/use/getting-the-most.md)** — start with the defaults and add capability as your needs grow
- **[Authoring Reference](docs/use/authoring.md)** — workflow scripts and context assembly scripts
- **[Importing Content](docs/use/importing-content.md)** — import monitoring, queue controls, and timing configuration
- **[Security Considerations](docs/setup/security.md)**
- **[Upgrading](docs/setup/upgrading.md)**
- **[Release Notes](RELEASE_NOTES.md)**

### Contributing

- **[Architecture Overview](docs/development/architecture.md)**
- **[Development Setup](docs/development/dev-setup.md)**

## Requirements

- Docker Engine or Docker Desktop
- Access to a supported cloud or local model
- Comfort with the terminal

## Roadmap

Future work is focused on UI enhancements, stronger research and retrieval, richer session memory and interactive chat, broader file and multimodal support, more efficient automation, and carefully scoped household or team use.

## License

MIT — see [LICENSE](LICENSE).
