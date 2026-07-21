---
name: framer
description: >
  Use when the user wants to design, edit, or publish a website or web page — creating layouts, editing sections, updating text or images, managing CMS collections and content, syncing external data, creating or modifying code components, managing color and text styles, handling localization, or publishing deployments. Trigger when the user mentions Framer, references their website or web pages, asks to edit designs, update site content, or work with any Framer project — even if they don't explicitly say 'Framer'.
  **Mandatory precondition**: run `npx @framer/agent@latest setup` and let it complete **BEFORE** loading this skill.
allowed-tools:
  - 'Bash(npx @framer/agent:*)'
  - 'Bash(npx @framer/agent@latest:*)'
  - 'Read(C:\Users\11\AppData\Local\Temp\framer/*)'
  - 'Write(C:\Users\11\AppData\Local\Temp\framer/*)'
  - 'Read(C:\Users\11\.agents\skills\framer/projects/**)'
  - 'Read(C:\Users\11\.agents\skills\framer/start-conversation.md)'
---

If you didn't run this command before loading the skill, run it now:
```bash
npx @framer/agent@latest setup
```

## Capabilities

What you can do with the Framer CLI:

- **Canvas Editing**: For design tasks — creating or editing pages, sections, layouts, recreating designs from screenshots, etc.
- **CMS**: Create, read, update, delete collections and items. Sync external databases.
- **Styles**: Manage color and text styles. Sync design systems.
- **Code Components**: Create, edit, type-check, and add custom React components to the canvas.
- **Assets**: Upload and manage images and files.
- **Localization**: Manage translations programmatically.
- **Data**: Store metadata on nodes and projects for plugin state.
- **Screenshots**: Capture node screenshots as PNG/JPEG. Export nodes as SVG.
- **Publishing**: Publish projects, manage deployments, track changes.

## CLI Usage

### Permissions

The Framer CLI requires network access (to download packages and connect to Framer servers) and file-system access outside the project directory (to install skills into `~/.agents/skills/` and `~/.claude/skills/`). In sandboxed environments, you must execute `npx @framer/agent@latest` commands with elevated permissions from the get-go. Do not try to execute them with the default sandboxed permissions; they will hang or fail.

### Session Management

If the user refers to a project by name or description rather than giving a URL, first inspect the recent projects:

```bash
npx @framer/agent@latest project list
```

Use that list to infer the likely project from the names and recency. If the right project is already known, use its project ID with `session new`. If there are multiple possible matches, clarify with the user. Only ask the user for a Project URL if there is no clear match. Avoid talking about the technical contents of returned data like IDs.

Each session maintains a persistent connection to a Framer project. Use sessions to keep state separate between different tasks, persist data across multiple execute calls, and reuse the `framer` API instance without reconnecting.

Create a session against an existing project:

```bash
npx @framer/agent@latest session new "<url or id>"
```

This prints the session ID. You must always use that session ID with `-s <id>` for all subsequent commands. Using the same session preserves your `state` between calls.

To create a brand new empty project and connect to it:

```bash
npx @framer/agent@latest project new
npx @framer/agent@latest session new "<returned project id>"
```

To remix (duplicate) an existing project and connect to the copy:

```bash
npx @framer/agent@latest project remix "<url, project id, or remix link>"
npx @framer/agent@latest session new "<returned project id>"
```

List active sessions:

```bash
npx @framer/agent@latest session list
```

## Generated Project Context

`session new` refreshes project-specific prompt and context content under this installed skill:

```text
projects/<safeProjectId>/
  index.md
  project-inventory.md
  prompt/
  recipes.md
  metadata.json
```

The generated `project-inventory.md` includes a snapshot of project context from `framer.agent.getContext()`, including pages, components, CMS data, styles, fonts, icons, and IDs when available.
The source template for generated project files lives at `projects/__template__/`. Files ending in `.template.md` are rendered into generated files without the `.template` marker.

Before editing, read `projects/<safeProjectId>/index.md` first. It contains a **task map**: read every item marked required in its "Always" row, including `prompt/critical-reminders.md`, then the row that matches your work — and every additional row a multi-domain task touches. The map routes you to the exact `prompt/` sections, `recipes.md` entries, and implementation guides for that task; read only what the map points to.

Read `projects/<safeProjectId>/project-inventory.md` before using project-specific IDs, page paths, component names, CMS collection names, style preset names, or icon names. Treat it as a generated snapshot; when the project may have changed, use `npx @framer/agent@latest read-project` for fresh live project state.

Use `projects/<safeProjectId>/recipes.md` as reference material for static CMS, image, plugin data, localization, and limitations examples. Do not read all recipes by default; follow the pointers in the task map.

`safeProjectId` uses the project ID with characters outside `a-z`, `A-Z`, `0-9`, `_`, and `-` replaced by `-`.

During normal task execution, do not call `framer.agent.getSystemPrompt()` or `framer.agent.getContext()` yourself. `session new` already refreshed their output into the generated files.

If the user explicitly asks to prompt the Framer agent, use `startConversation`, or delegate a design task to Framer's agent, read `start-conversation.md`. Do not read it otherwise.

## Required Workflow

Every connected-project task follows these steps:

1. Run `session new` and keep the returned session ID.
2. Read the generated project `index.md` and follow its task map to the relevant sections.
3. Look up current API docs before every new API method use.
4. Execute code through the CLI with `-s <sessionId>`.
5. Store reusable results in `state`.
6. Review or read back changes before reporting completion.

### API Documentation

Run `npx @framer/agent@latest docs` before writing code that uses a method you have not already verified in this task. Do not guess method names or signatures.

```bash
npx @framer/agent@latest docs
npx @framer/agent@latest docs Collection
npx @framer/agent@latest docs Collection.getItems
npx @framer/agent@latest docs framer.agent.applyChanges
```

`docs` with no arguments lists available methods. Looking up a class shows its full definition without expanding referenced types. Looking up a specific method or type automatically expands referenced types recursively.

### Method Selection

Prefer `framer.agent.*` methods over regular plugin API methods when an agent-specific method exists.

- Use `npx @framer/agent@latest read-project` and `npx @framer/agent@latest apply-changes` when possible. It is still ok to call `framer.agent.readProject` or `framer.agent.applyChanges` in exec scripts if the task needs more complex logic than a plain CLI call.
- Use `framer.agent.getNode`, `getNodes`, `getNodesOfTypes`, `getDescendantsOfTypes`, `getDescendantReferencesOfTypes`, `getRect`, `getScopeNode`, `getGroundNode`, `getParentNode`, `getAncestors`, `serialize`, `serializeNodes`, and `paginate` for project tree reads. In exec scripts, use the VM globals `walkWithSkipChildren` and `getInnerText` for local traversal of serialized nodes.
- Do not use `npx @framer/agent@latest read-project` or `framer.agent.readProject` for node tree reads unless you have just checked current docs and confirmed the exact query type. Query shapes such as `{ type: "node-by-id" }` are not valid for the current local API.
- Use `framer.agent.readComponentControls`, `readIconSetControls`, `readIcons`, `readLayoutTemplateControls`, and `readShaderControls` for reading controls of components, icon sets, icons, layout templates, and shaders.
- Use `framer.agent.applyChanges` for page, layout, style, CMS-on-canvas, component, and design-token edits when possible. Do not use low-level node APIs like `createNode`, `setAttributes`, or `setRect` for design/layout work.
- Use `framer.agent.publish` for publishing. Do not use `publish`, `getDeployments`, or `deploy` for normal agent publishing flows.
- Prefer `framer.agent.applyChanges` and project tree read methods for CMS work where possible. Fall back to collection APIs only for functionality otherwise not supported. If you add collections or fields via collection APIs, some things may not work as expected when those collections or fields are then used on the canvas via `framer.agent.applyChanges`.
- Create styles, design tokens, components, and variables via `framer.agent.applyChanges`. Using plugin API methods can cause issues when trying to use newly created values later in `framer.agent.applyChanges` calls.
- Use generic `framer.*` plugin API methods only for capabilities without a CLI command or agent-specific counterpart, such as code file management, localization, and redirects.

### Execute Code

Prefer writing code to a unique file under `C:\Users\11\AppData\Local\Temp\framer/` and executing it with `-f`. Do not create code files with shell heredocs or `cat`.

Name files `<sessionId>-<short-summary>.js`, for example `1-read-collections.js`.

```bash
npx @framer/agent@latest exec -s 1 -f C:\Users\11\AppData\Local\Temp\framer/1-read-collections.js
```

For short snippets, `exec` also accepts `-e <code>` or code piped on stdin.

### Use `state`

Always save results you will need again. API calls are slow; do not repeat them.

```js
state.collections = await framer.getCollections();
```

### Runtime Notes

- `framer` is the connected Framer Server API instance.
- `state` is an object persisted between exec calls within your session.
- `console` is available for output.
- `require` can load sandboxed Node.js modules: fs, path, url, crypto, buffer, util, os.
- Standard globals include `fetch`, `Buffer`, `URL`, `crypto`, and `setTimeout`.
- `fs` operations are sandboxed to cwd, `/tmp`, and `os.tmpdir()`.

### Shell Quoting

In Windows PowerShell, if an argument contains nested quotes, use a single-quoted here-string and pass the variable. Do not backslash-escape quotes.

```powershell
$value = @'
[{"key":"value","filter":["text","$rect"]}]
'@
npx @framer/agent@latest <command> --option $value
```

## Core Usage Principles

- Be concise. Do the work and report user-facing results, not internal field IDs or escaping details.
- Use `framer.*` for plugin API calls. Top-level methods are not globals.
- When command output includes `[FRAMER_BRANCH_CHANGE]`:
  - If you did not call `switchBranch` or `createBranch` yourself and the name follows the default `adjective-noun` pattern, use `renameBranch` to rename with a concise title describing the current task.
  - Tell the user the active branch changed and include the `url` value.
- Before making changes that add, update, or delete content the user did not clearly request, explain the planned change and ask for confirmation.
- Always ask for confirmation before destructive actions that the user did not explicitly request.

## Compaction

If an agent conversation is compacted during a connected-project task, read this skill, the generated project `index.md`, and the sections its task map points to back into context before continuing. This instruction itself must not be lost during compaction.
