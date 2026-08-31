# Agent Skills (bundled for Voice Typer)

This directory contains 18 agent skills pre-bundled from the open agent skills ecosystem (skills.sh). They are checked into the repo so the cloud AI agent has them out of the box — no `npx skills add` needed.

## How to use

1. Read the relevant `SKILL.md` file before touching a domain:
   ```
   .Skills/<skill-name>/SKILL.md
   ```
2. Apply the skill's workflow/checklist while working.
3. Read lazily — only for skills relevant to files you're about to touch.

## Skill list

| Folder | Source | Installs | Domain |
|--------|--------|----------|--------|
| `systematic-debugging` | obra/superpowers | 241.4K | Root cause investigation |
| `code-review-and-quality` | addyosmani/agent-skills | 33.8K | Code review |
| `test-driven-development` | addyosmani/agent-skills | 27.5K | TDD |
| `documentation-and-adrs` | addyosmani/agent-skills | 28.1K | Docs/ADRs |
| `pytest-coverage` | github/awesome-copilot | 12.5K | Python testing |
| `javascript-typescript-jest` | github/awesome-copilot | 12.5K | TS/Jest testing |
| `e2e-testing` | affaan-m/ecc | 9.2K | E2E testing |
| `typescript-advanced-types` | wshobson/agents | 68.1K | Advanced TS types |
| `react-vite-best-practices` | asyrafhussin/agent-skills | 2.3K | React/Vite |
| `frontend-design-ui-ux` | ulpi-io/skills | 2.2K | UI/UX design |
| `accessibility` | affaan-m/ecc | 6.9K | WCAG a11y |
| `rust-engineer` | jeffallan/claude-skills | 5.2K | Rust/Tauri |
| `python-best-practices` | alleneubank/claude-code | 2K | Python |
| `sqlite-database-expert` | martinholovsky/claude-skills-generator | 2.8K | SQLite |
| `voice-audio-engineer` | curiositech/some_claude_skills | 341 | Voice/audio |
| `security-review` | affaan-m/ecc | 15.6K | Security audit |
| `internationalization-i18n` | mindrally/skills | 857 | i18n |
| `git-workflow` | mindrally/skills | 704 | Git workflow |

**Note:** These skills are downloaded from the open skills ecosystem (via `npx skills find`). Each is a markdown guide with workflows and checklists — they do not contain executable code. See `skills.sh` for each skill's source repository.

## Updating

To refresh or add skills, run `npx skills find <query>` and `npx skills add <owner/repo@skill>`, then copy the installed skill folder (from `.agents/skills/<name>/`) into `.Skills/<name>/`.
