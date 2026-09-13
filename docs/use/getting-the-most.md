# Getting the Most from Assistant.md

Assistant.md is designed in layers. Once it is deployed and connected to a model, its defaults give you a capable agent workspace for working with your vault, researching, creating documents, and carrying out multi-step tasks.

Start with the outcome, not the configuration. Use the default capabilities first. If they do not produce what you need or the quality you expect, add the layer that addresses the gap: project context, a reusable skill, a connected service, advanced command-line tools, delegation, session search, or automation. You do not need to understand or enable everything at once.

This progression reflects a core design principle: maximum user control with minimal magic. Capabilities, instructions, and access are added deliberately, so a simple setup remains understandable and a more powerful assistant grows from choices you made.

Self-hosting does require comfort with Docker and the terminal, along with enough knowledge to secure the deployment appropriately. That is the main setup threshold. Once Assistant.md is running, its default capabilities are immediately useful and the remaining layers can be adopted gradually.

The chat agent can help with the setup described in this guide. Tell it what you want to accomplish, and it can help organize a workspace, draft instructions and skills, or create an automation for you to review.

## Chat and Vault Explorer

Open Assistant.md, choose a vault and model, and start talking. Alongside chat, use Vault Explorer to browse, preview, upload, move, and edit vault files directly. The agent can work with the same files using its built-in tools.

With no additional customization, Chat and Vault Explorer let you:

- browse, search, and preview Markdown across your vaults;
- create, edit, upload, move, and organize files directly;
- import PDFs and public URLs as durable Markdown;
- ask the agent to find, compare, or summarize information across your notes;
- research a topic, draft documents, and save useful results back to the vault; and
- carry out longer, multi-step tasks using the agent's available tools.

Describe the outcome you want, important constraints, and where useful work should be saved. For a substantial task, ask the agent to establish a goal so it can track the objective and continue working until it is resolved.

## Make chat project-aware

When work belongs to a particular project, set that project folder as the chat workspace. A workspace gives the agent a useful starting point without preventing it from working elsewhere in the vault when needed.

Use built-in file conventions to give chat durable project context and working preferences. The default setup recognizes a small set of files with distinct purposes:

- `{workspace}/README.md` explains the project: its purpose, current state, important files, terminology, and active priorities.
- `{workspace}/playbook.md` defines project-specific working rules, such as review expectations, file conventions, or sources of truth.
- `AssistantMD/playbook.md` defines preferences and policies that should apply across the vault.
- `AssistantMD/soul.md` shapes the agent's personality, tone, and communication style.

Start with a workspace `README.md` when the agent needs orientation. Add a local `playbook.md` when it needs rules. Use the vault-wide playbook only for guidance that genuinely applies everywhere.

These files are ordinary Markdown. Ask the agent to draft or improve them, then review the result as you would any other lasting instruction.

## Teach repeatable procedures with skills

A skill is a reusable set of instructions for a task. Skills are useful when you repeat the same explanation, need a dependable sequence of steps, or want a consistent output format.

Examples include:

- preparing a weekly project update;
- turning meeting notes into decisions and action items;
- reviewing a document against a house style;
- researching a topic and recording sources consistently; or
- processing files according to a naming and filing convention.

The default setup discovers skills stored as either `AssistantMD/Skills/skill-name/SKILL.md` or `AssistantMD/Skills/skill-name.md`. It adds their YAML frontmatter descriptions to the agent's instructions so commonly used skills can be identified and opened quickly without loading every skill in full.

Skills do not have to live in that canonical folder. You can keep a specialized skill anywhere in the vault, including beside the project it supports, and point chat to that file when you want it used. These skills are not advertised automatically, but they are useful when instructions apply only to one workspace or are needed infrequently.

A useful skill states the goal, the relevant locations, the procedure, the expected output, and anything the agent must not change.

You do not need to author a skill from scratch. Ask Assistant.md to turn a successful process into a reusable skill, then review and test it on a representative task.

## Expand what the agent can do

Assistant.md's built-in tools cover vault work, web research, content import, workflow execution, and other common tasks. Connections and advanced execution extend that foundation.

### Connect services and remote tools

Under **System → Connections**, you can connect Google accounts and compatible MCP servers. Gmail connections let the agent search and read mail, inspect threads, save permitted PDF attachments, and create recipient-free unsent drafts when those capabilities are enabled. MCP connections can provide tools from other services while keeping authentication and tool permissions scoped to each connection.

Use the [Connections guide](connections.md) for account setup, authentication, testing, and permission choices.

### Enable the advanced shell

Advanced mode gives primary chat access to a separate, non-root Linux environment. It can use command-line tools, install packages, work in persistent shell storage, and run local stdio MCP providers without exposing the application host directly.

This is the largest capability increase in v0.8.0, but it is not required for ordinary vault work. Enable it when a task benefits from a CLI, a language runtime, a specialized package, or an MCP provider that runs locally. Keep host mounts narrow and grant access only to the files the shell needs.

Follow [Enable advanced mode](../setup/installation.md#enable-advanced-mode) for setup and safety guidance. Once it is ready, ask chat to use the bundled **Advanced Shell MCP Setup** skill when installing a local MCP provider.

## Delegate independent work

Use delegation intentionally when independent parts of a larger task would benefit from separate attention. Ask chat to send bounded research, analysis, or review work to subagents, then use their findings in the primary task.

Delegation is useful for comparing independent approaches, researching several questions in parallel, or asking another agent to review completed work. It adds coordination and model usage, so ordinary tasks are usually better handled directly in the primary chat.

Delegate agents use explicitly available built-in tools. They cannot use connected MCP tools or the advanced shell, so keep those steps in primary chat.

## Automate recurring work

Use a workflow when a process should run on a schedule or needs more structure than conversational instructions provide. Workflows are a good fit for repeatable sequences, conditional routing, periodic reviews, and tasks where consistent execution or cost control matters.

Examples include:

- reviewing an intake folder each evening;
- classifying new material and routing it into project folders;
- checking documents for missing metadata or broken links;
- producing a recurring digest; or
- refreshing a collection of research notes.

Reusable and scheduled workflows live in `AssistantMD/Authoring/`. A workflow tied to one project can live beside that project's files and be run explicitly. Manual and scheduled runs appear on the Dashboard, where their progress and results can be reviewed.

Describe the process to the chat agent and ask it to draft the workflow. Test it manually with representative inputs before enabling a schedule. See the [Authoring reference](authoring.md) for the workflow format and available runtime helpers.

## Search past sessions

Session summaries let chat search for relevant past work without loading entire transcripts. The `session_ops` tool can search existing summaries or refresh them when you want earlier decisions, research, or project history brought into the current conversation.

Assistant.md includes an optional nightly workflow that keeps these summaries up to date. Enable `system/nightly-session-summarization` under **Dashboard → Workflows** if you want automatic summary extraction. Session-summary search currently requires the default OpenAI embedding model and an `OPENAI_API_KEY` configured under **System → Secrets**.

## Customize context only when needed

A context assembly script controls what the chat agent receives at the beginning of a conversation. It can combine recent messages, selected files, skills, project guidance, and summary records.

Most users do not need to change the default context script. Workspace files, playbooks, skills, connections, and workflows cover the common customization needs with less complexity. Consider a custom context script only when you need a distinctly different context strategy, such as selecting project material according to custom rules or supporting separate working styles.

Context scripts live in `AssistantMD/Authoring/` and declare `run_type: context`. Ask the agent to draft one and use the [Authoring reference](authoring.md) to review its behavior before making it your default.

## Keep the outcome at the center

Begin each new kind of work with the defaults and let the result reveal what is missing. Add a layer when you need better context, more consistent quality, access to another service, specialized tools, past-session recall, or repeatable automation. As your ambitions grow, these layers become one shared capability set: start a chat, describe the outcome, and ask Assistant.md to recommend an approach while you retain control over its access and proposed work.
