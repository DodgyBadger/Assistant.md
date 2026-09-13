# Assistant.md

**A self-hosted, Markdown-first agent workspace for knowledge work.**

> [!IMPORTANT]
>
> **v0.8.0 is a major agentic upgrade.** Assistant.md can now connect to remote MCP services, run local MCP providers and commands in an optional advanced environment, work with Gmail, and securely manage the credentials those capabilities require. Chat is now the control surface for a substantially more capable agent system.

Assistant.md gives AI agents a place to do sustained work with your notes, research, tools, and repeatable processes—not merely answer questions in chat. Choose any folder in your vault as a project workspace; the agent can follow local `README.md` and `playbook.md` guidance, work across the vault when needed, and leave useful results behind as durable Markdown.

Your files remain readable, portable, and useful without Assistant.md. The system around them provides project context, connected tools, automation, and reviewable execution while keeping agent capabilities explicit and bounded.

## Features

- **Agentic work sessions:** Run long-lived, tool-heavy work with reconnectable execution, context overflow protection, bounded delegation, goals, and durable session history.
- **Project-aware workspaces:** Scope a chat to any vault folder and provide project-specific guidance through familiar Markdown files such as `README.md` and `playbook.md`.
- **Connected MCP tools:** Add remote Streamable HTTP or SSE servers with per-connection authentication, testing, tool allowlists, and on-demand tool discovery.
- **Advanced execution:** Optionally give primary chat a persistent Linux workspace for CLIs, runtimes, bounded commands, and local stdio MCP providers without exposing the application host directly.
- **Gmail connections:** Search and read mail, inspect threads, save bounded PDF attachments, and create unsent drafts across one or more explicitly connected accounts.
- **Markdown knowledge base:** Browse, preview, edit, upload, move, organize, and search portable files in isolated vaults.
- **Research and ingestion:** Search, extract, and crawl web content, then turn public pages and vault PDFs into durable Markdown.
- **Composable automation:** Build sandboxed Python workflows and context assembly scripts, keep project workflows beside their content, and schedule reusable workflows.
- **Reviewable and recoverable changes:** Inspect proposed file edits before applying them, restore revisions, or roll back changes from completed activity.
- **Operational visibility:** Review workflow history, running work, tool details, and searchable System Activity.
- **Flexible model support:** Use supported cloud or local models, including multimodal models and experimental OpenAI OAuth.
- **Explicit security controls:** Store credentials encrypted, authorize connections independently, configure ingress authentication, and keep restricted execution as the default.
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
