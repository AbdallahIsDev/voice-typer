# Dev Container

Voice Typer includes a dev container configuration for VS Code Remote - Containers and GitHub Codespaces. This provides a fully reproducible development environment without needing to install Python, Node.js, or system dependencies locally.

## Quick Start

1. Install [Docker](https://docker.com) and the [VS Code Remote - Containers extension](https://marketplace.visualstudio.com/items?itemName=ms-vscode-remote.remote-containers).
2. Open the project in VS Code.
3. When prompted, click **Reopen in Container** (or run the **Dev Containers: Reopen in Container** command).
4. VS Code builds the Docker image (first time only, ~3-5 minutes) and opens the project inside the container.

## What's Included

The container (`Dockerfile.dev`) is based on the official Python 3.12 image and includes:

- **Python 3.12** — for the backend (`voice_typer/server/`)
- **Node.js 20** — for the Electron client (`voice_typer/client/`)
- **System dependencies** — PortAudio, X11 libs, etc. for audio/hotkey support
- **Zsh** with common utilities (oh-my-zsh, history, etc.)

## Post-Create Setup

The `postCreateCommand` in `devcontainer.json` automatically runs:

```bash
pip install --user -e '.[test,dev]'   # Install Python deps in editable mode
cd voice_typer/client && npm ci        # Install Node deps
```

After this completes, you can start development immediately:

```bash
# Start the Electron app in dev mode (starts both Python backend + Electron)
cd voice_typer/client && npm run dev

# Run Python tests
pytest tests/ -v

# Run frontend tests
cd voice_typer/client && npm run test

# Type-check the frontend
cd voice_typer/client && npm run typecheck
```

## VS Code Extensions

The container auto-installs these extensions:

| Extension | Purpose |
|-----------|---------|
| `ms-python.python` | Python language support, IntelliSense, debugging |
| `ms-python.mypy-type-checker` | Static type checking |
| `charliermarsh.ruff` | Python linting + formatting |
| `dbaeumer.vscode-eslint` | TypeScript/JavaScript linting |
| `esbenp.prettier-vscode` | Code formatting (TS/CSS/JSON) |
| `bradlc.vscode-tailwindcss` | Tailwind CSS IntelliSense |
| `ms-azuretools.vscode-docker` | Docker management |

## Editor Settings

The container configures:

- **Python**: Ruff as default formatter, format-on-save enabled
- **TypeScript/TSX**: Prettier as default formatter, format-on-save enabled
- **Tab size**: 2 spaces (matches `biome.json`)
- **Trim trailing whitespace** on save
- **Insert final newline** on save

## Limitations

- **Audio devices**: The container doesn't have access to the host's microphone. Audio-dependent tests will fail; use `@pytest.mark.skip` for those.
- **GUI apps**: Electron's GUI won't display inside the container without X11 forwarding. The dev container is best for running tests, linting, and type checking — run `npm run dev` on your host machine for GUI testing.
- **Docker performance**: On macOS/Windows, Docker's file system can be slow with large `node_modules/`. The container uses a named volume for `node_modules` to mitigate this.

## Troubleshooting

### Container build fails

```bash
# Rebuild from scratch (clears Docker cache)
Dev Containers: Rebuild Container Without Cache
```

### `npm ci` fails

Ensure `voice_typer/client/package-lock.json` is committed and up to date:

```bash
cd voice_typer/client && npm install && git diff package-lock.json
```

### Python imports fail

The editable install (`pip install -e .`) should handle all imports. If it fails:

```bash
pip install --user -e '.[test,dev]'
```
