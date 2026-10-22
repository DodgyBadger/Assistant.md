# Getting the Most from Assistant.md

Assistant.md is designed in layers. Once it is deployed and connected to a model, its defaults support working with your vault, researching, creating documents, and carrying out multi-step tasks.

Start with the outcome, not the configuration. Use the defaults first, then add the layer that addresses a demonstrated need: better project context, a reusable procedure, access to another service, specialized tools, or automation. Capabilities, instructions, and access are added deliberately, keeping you in control of how the assistant works.

Describe what you want to accomplish to the chat agent. It can recommend an approach, help prepare any supporting files or connections, and test the result with you.

## Chat and Vault Explorer

Open Assistant.md, choose a vault and model, and start talking. Chat and Vault Explorer work with the same vault files, so you can inspect and organize material yourself while asking the agent to help develop it.

With no additional customization, you can:

- ask the agent to find, compare, or summarize information across your notes;
- research a topic, draft documents, and save useful results back to the vault; and
- carry out longer, multi-step tasks using the agent's available tools.

Describe the outcome, important constraints, and where useful work should be saved. For substantial work, ask the agent to establish a goal so it can track progress toward that outcome.

## Make chat project-aware

Set a project folder as the chat workspace when the agent needs orientation to ongoing work. The workspace supplies a starting point without restricting the agent to that folder.

Use these files to give future conversations durable context and working preferences:

- `{workspace}/README.md` explains the project: its purpose, current state, important files, terminology, and active priorities.
- `{workspace}/playbook.md` defines project-specific working rules, such as review expectations, file conventions, or sources of truth.
- `AssistantMD/playbook.md` defines preferences and policies that should apply across the vault.
- `AssistantMD/soul.md` shapes the agent's personality, tone, and communication style.

These are ordinary Markdown files that you and the agent can maintain as the work develops.

## Teach repeatable procedures with skills

Use a skill when you want the agent to repeat a successful approach, such as preparing a weekly project review or editing documents to a house style. A skill records the goal, relevant paths, procedure, expected result, and anything the agent should avoid changing. Ask the agent to turn a successful task into a focused skill, then try it on representative work.

The default setup discovers skills stored as either `AssistantMD/Skills/skill-name/SKILL.md` or `AssistantMD/Skills/skill-name.md`. Their YAML frontmatter descriptions are advertised to the agent, which can open the full instructions when relevant.

A specialized skill can also live elsewhere in the vault, including beside a project. Point the agent to it explicitly: skills outside the discovered locations are not advertised automatically.

## Expand what the agent can do

Add access or tools when the work requires information or capabilities beyond the default setup.

### Connect services and remote tools

Connections let the agent work with information held outside your vault. For example, a Gmail connection can support email research and drafting, while compatible MCP servers can provide access to other services. Gmail drafts remain unsent, and the available actions depend on the connection's permissions.

Use the [Connections guide](connections.md) to choose and configure the access your task needs.

### Enable the advanced shell

Use advanced mode when a task needs command-line tools, a language runtime, specialized packages, or a locally running MCP provider. It gives primary chat a separate, non-root Linux environment with persistent working storage. Keep host mounts limited to the files the task needs.

Follow [Enable advanced mode](../setup/installation.md#enable-advanced-mode) for setup. The bundled **Advanced Shell MCP Setup** skill helps chat install local MCP providers.

MCP tools and the advanced shell are available to primary interactive chat. Keep those steps there when planning workflows or delegated work.

## Delegate independent work

Delegation helps when parts of a task can be researched, analyzed, or reviewed independently. Ask chat to assign bounded questions to subagents and bring their findings back into the main task. Comparing approaches or obtaining a separate review can improve the result, but delegation adds coordination and model usage. Delegate agents use explicitly available built-in tools.

## Automate recurring work

Use a workflow when an established process should run unattended, on a schedule, or as a defined processing pipeline. Examples include reviewing an intake folder each evening or producing a recurring digest.

Skills and workflows can support the same goal. A skill can guide a project review when you ask for it; a workflow can run its defined steps without an ongoing chat. Choose according to how the work should run.

Reusable and scheduled workflows live in `AssistantMD/Authoring/`. A project-local workflow can live beside the project's files and be run explicitly. Review run progress and results on the Dashboard, and test with representative inputs before enabling a schedule. The [Authoring reference](authoring.md) covers script structure and runtime helpers.

## Search past sessions

Ask the agent to find earlier decisions, research, or discussions when you want to continue previous work. The session browser also lets you search past conversations directly.

The agent's [session search tool](../tools/session_ops.md) searches words and phrases across session metadata, transcripts, and session maps. Short conversations remain searchable without a map. Use original messages to verify important details rather than relying only on a map's interpretation.

## Customize context only when needed

A context assembly script controls which conversation history, files, and guidance are supplied to the agent. Customize it when the default loading policy cannot provide the context your work needs, such as selecting project material according to custom rules.

Most users can keep the default script and use workspace files, playbooks, and skills to supply context. Context scripts live in `AssistantMD/Authoring/`; consult the [Authoring reference](authoring.md) when a different assembly strategy is needed.
