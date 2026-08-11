# Credential Store

> **Status**: Implemented. Encrypted at rest via the OS keychain
> on Windows / macOS / Linux. Plaintext fallback with `0o600` perms
> when no keyring backend is available.

## Overview

Voice Typer stores API keys for cloud providers (OpenAI, Groq,
Deepgram) and the LLM polishing service in the OS-native credential
backend via the [`keyring`](https://pypi.org/project/keyring/) library:

| Platform | Backend                                     | Library / Daemon                              |
| -------- | ------------------------------------------- | --------------------------------------------- |
| Windows  | Windows Credential Manager                  | `pywin32` (bundled with `keyring`)            |
| macOS    | Keychain                                    | `pyobjc` (bundled with `keyring`)             |
| Linux    | Secret Service (libsecret / GNOME Keyring)  | `python-dbus` + `gnome-keyring-daemon` running |

When no usable backend is available (most commonly on a headless Linux
container without `gnome-keyring-daemon`), the store falls back to the
legacy behavior: plaintext in `config.json` with `0o600` permissions on
POSIX (the file is already created with `0o600` by `_secure_atomic_write`
in `config.py`). On Windows the fallback is plaintext under
`%APPDATA%\voice-typer\config.json` (per-user ACLs already isolate the
file).

## Architecture

```
                ┌──────────────────────────────────────┐
                │  Renderer (React)                    │
                │  - API key forms                     │
                │  - KeyringStatusBadge component      │
                │  (reads config.keyring_status)       │
                └──────────────┬───────────────────────┘
                               │ IPC: set_config({ openai_api_key: "sk-..." })
                               ▼
                ┌──────────────────────────────────────┐
                │  service.apply_config()              │
                │  - Detects api_key fields            │
                │  - Calls credential_store.store_secret│
                │  - setattr(app.config, field, value) │
                │  - app.config.save()                 │
                └──────────────┬───────────────────────┘
                               │
                               ▼
                ┌──────────────────────────────────────┐
                │  credential_store.store_secret()     │
                │  - Try keyring.set_password()        │
                │  - On failure: write to config.json  │
                │    with 0o600 perms (POSIX)          │
                └──────────────┬───────────────────────┘
                               │
                               ▼
                ┌──────────────────────────────────────┐
                │  config.py: Config.save()            │
                │  - For each api_key field:           │
                │    if keyring available:             │
                │      store_secret(provider, value)   │
                │      replace value with "keyring://  │
                │      <provider>" reference token     │
                │    else:                             │
                │      leave plaintext (0o600 perms)   │
                └──────────────────────────────────────┘
```

## On-disk format

### After migration (keyring available)

`config.json` contains only reference tokens, never the actual secret:

```json
{
  "schema_version": 2,
  "hotkey": "<caps_lock>",
  "openai_api_key": "keyring://openai",
  "groq_api_key": "keyring://groq",
  "deepgram_api_key": "keyring://deepgram",
  "cloud_api_key": "",
  "llm_api_key": "keyring://llm",
  "secrets_migrated": true
}
```

The real secret lives in the OS keychain under the service name
`voice-typer` with the provider name as the username key.

### After migration (keyring unavailable — plaintext fallback)

`config.json` retains the plaintext values (with `0o600` perms on POSIX):

```json
{
  "schema_version": 2,
  "hotkey": "<caps_lock>",
  "openai_api_key": "sk-...",
  "groq_api_key": "gsk-...",
  "deepgram_api_key": "",
  "cloud_api_key": "",
  "llm_api_key": "",
  "secrets_migrated": true
}
```

The `secrets_migrated` flag is set to `true` ONLY when migration
succeeds OR when there is no plaintext to skip (i.e. nothing to
migrate). When keyring is unavailable AND real plaintext keys are
present, the flag is NOT set — instead a diagnostic flag
`secrets_migrated_keyring_was_unavailable` is recorded and migration
is DEFERRED: the next launch (once a keyring backend becomes
available, e.g. the user installs `gnome-keyring-daemon`) automatically
re-runs migration. No user intervention is required — the plaintext
keys do NOT persist forever.

An operator can still force an earlier re-migration attempt by
manually clearing the flag in `config.json`:

```json
{ "secrets_migrated": false }
```

The next app launch will then migrate the plaintext keys to keyring.

## API

The credential store module (`voice_typer/server/credential_store.py`)
exposes:

```python
KEYRING_SERVICE_NAME = "voice-typer"
KEYRING_REF_PREFIX = "keyring://"
PROVIDER_TO_CONFIG_FIELD = {
    "openai": "openai_api_key",
    "groq": "groq_api_key",
    "deepgram": "deepgram_api_key",
    "cloud": "cloud_api_key",
    "llm": "llm_api_key",
}


def store_secret(provider: str, value: str) -> bool:
    """Store a secret. Returns True if stored in keyring, False if
    fell back to plaintext in config.json. Never raises."""


def load_secret(provider: str) -> str | None:
    """Load a secret. Returns the value, or None if not found.
    Tries keyring first, falls back to config.json. Never raises."""


def delete_secret(provider: str) -> None:
    """Delete a secret from both keyring and config.json. Never raises."""


def migrate_secrets_to_keyring() -> int:
    """One-time migration: read plaintext API keys from config.json,
    store them in keyring, replace with keyring:// references.
    Returns count of secrets moved to keyring. Idempotent (gated by
    the secrets_migrated flag in config.json)."""


def is_keyring_available() -> bool:
    """True if a usable keyring backend is installed (not the fail
    backend). Cached for the lifetime of the process."""


def get_keyring_status() -> dict:
    """Returns {
        'available': bool,
        'backend': str | None,  # e.g. 'SecretServiceKeyring'
        'fallback': bool,        # True when plaintext fallback is in use
        'reason': str | None,    # short diagnostic when unavailable
    }. Attached to get_config IPC response as 'keyring_status'."""
```

## Privacy guarantees

1. **Secret values are never logged.** Only metadata (provider name,
   value length, keyring-vs-fallback status) appears in log messages.
   See `test_store_secret_never_logs_value` in
   `tests/test_credential_store.py`.

2. **Secret values are never echoed over IPC.** The existing
   `_sanitize_config_for_ipc` in `ipc_server.py` replaces secret
   fields with `"<redacted>"` in `get_config` responses. doesn't
   change this — the renderer still sees `"<redacted>"` for any set
   key, regardless of whether it's in keyring or plaintext fallback.

3. **The on-disk `config.json` never contains secrets when keyring is
   available.** Only `keyring://<provider>` reference tokens are
   written. A backup of `config.json` to cloud storage (Time Machine,
   OneDrive, etc.) won't leak the secrets.

4. **The plaintext fallback uses `0o600` perms on POSIX.** This is
   enforced by `_secure_atomic_write`.
   The file is owner-read/write only — not world-readable.

5. **Migration is one-shot.** The `secrets_migrated` flag in
   `config.json` ensures the migration doesn't run on every launch
   (which would be wasteful and could re-migrate keys the user has
   since deleted).

## Testing

### Automated tests

```bash
# Run the credential store unit tests (mocked keyring)
cd /home/z/my-project/voice-typer
python -m pytest tests/test_credential_store.py -v --timeout=30

# Verify existing config tests still pass (no regression)
python -m pytest tests/test_config.py -v --timeout=30
```

The unit tests mock the `keyring` library so they don't depend on a
real OS keychain backend (which is unavailable in the CI container).
They cover:

- `store_secret` / `load_secret` / `delete_secret` calling keyring
  with the right service / provider args.
- Fallback to `config.json` (with `0o600` perms on POSIX) when keyring
  raises or is unavailable.
- `migrate_secrets_to_keyring` moving keys from `config.json` to
  keyring and replacing them with `keyring://` references.
- Migration idempotency (running twice doesn't double-store).
- Secret values never appearing in log messages.

### Manual testing on each platform

The keyring library's backend selection depends on the OS and the
availability of native daemons. The following runbooks describe how to
verify the credential store works correctly on each platform.

#### Linux (with GNOME Keyring)

> **VALIDATE ON LINUX DISPLAY HOST** — requires a graphical session
> with `gnome-keyring-daemon` running. Cannot run in a headless
> container.

1. Install the dependencies:

   ```bash
   # Debian / Ubuntu
   sudo apt install gnome-keyring python3-dbus
   # Fedora
   sudo dnf install gnome-keyring python3-dbus
   ```

2. Ensure `gnome-keyring-daemon` is running (it usually starts
   automatically when you log in to a GNOME / KDE / XFCE session). To
   verify:

   ```bash
   echo $DBUS_SESSION_BUS_ADDRESS
   # Should print something like: unix:path=/run/user/1000/bus
   busctl --user list | grep -i keyring
   # Should list: org.freedesktop.secrets
   ```

3. Run the app and enter an OpenAI API key in Settings → Models.

4. Verify the key is NOT in `~/.config/voice-typer/config.json`:

   ```bash
   grep -E 'api_key' ~/.config/voice-typer/config.json
   # Should show: "openai_api_key": "keyring://openai", ...
   ```

5. Verify the key IS in the GNOME Keyring:

   ```bash
   secret-tool search service voice-typer
   # Should list: username = openai, secret = [hidden]
   ```

6. Restart the app and verify the key is still loaded (Settings →
   Models shows "configured").

#### macOS (Keychain)

> **VALIDATE ON MACOS HOST** — requires a macOS graphical session.
> Cannot run in a Linux container.

1. Install the app (the `pyobjc` deps are pulled in automatically via
   `keyring`).

2. Run the app and enter an OpenAI API key in Settings → Models.
   macOS will show a Keychain access prompt — click "Always Allow".

3. Verify the key is NOT in
   `~/Library/Application Support/voice-typer/config.json`:

   ```bash
   grep -E 'api_key' ~/Library/Application\ Support/voice-typer/config.json
   # Should show: "openai_api_key": "keyring://openai", ...
   ```

4. Verify the key IS in the Keychain:

   ```bash
   security find-generic-password -s voice-typer -a openai
   # Should print the keychain entry metadata
   ```

5. Restart the app and verify the key is still loaded.

#### Windows (Credential Manager)

> **VALIDATE ON WINDOWS HOST** — requires a Windows graphical session.
> Cannot run in a Linux container.

1. Install the app (the `pywin32` deps are pulled in automatically via
   `keyring`).

2. Run the app and enter an OpenAI API key in Settings → Models.

3. Verify the key is NOT in
   `%APPDATA%\voice-typer\config.json`:

   ```powershell
   Select-String -Path "$env:APPDATA\voice-typer\config.json" -Pattern 'api_key'
   # Should show: "openai_api_key": "keyring://openai", ...
   ```

4. Verify the key IS in Credential Manager:

   ```powershell
   cmdkey /list
   # Look for an entry with Target: voice-typer:openai
   ```

5. Restart the app and verify the key is still loaded.

#### Linux (headless — plaintext fallback)

This is the default behavior in CI containers and headless servers
without a desktop environment.

1. Ensure no `gnome-keyring-daemon` is running and `python3-dbus` is
   not installed (or the `DBUS_SESSION_BUS_ADDRESS` env var is unset).

2. Run the app and enter an API key.

3. Verify the key IS in `~/.config/voice-typer/config.json`:

   ```bash
   grep 'openai_api_key' ~/.config/voice-typer/config.json
   # Should show the actual key value (plaintext fallback)
   ```

4. Verify the file has `0o600` perms:

   ```bash
   stat -c '%a' ~/.config/voice-typer/config.json
   # Should print: 600
   ```

5. Verify the renderer shows the amber "Plaintext" warning badge next
   to the API key input (not the green "Secure" lock icon).

## Migration from config files

Existing `config.json` files with plaintext API keys are
auto-migrated on the next app launch:

1. `Config.load()` reads `config.json`.
2. If `secrets_migrated` is `false` (or absent files),
   `credential_store.migrate_secrets_to_keyring()` is called.
3. For each `*_api_key` field with a non-empty, non-`keyring://`
   value:
   - If keyring is available: the value is stored in keyring and the
     field is replaced with `"keyring://<provider>"`.
   - If keyring is unavailable: the value is left as plaintext (the
     user has been warned via the renderer's amber badge).
4. `secrets_migrated` is set to `true` in `config.json`.
5. The in-memory `Config` instance still has the real values (loaded
   from keyring or plaintext) so `cloud_engines` / `llm_polish` /
   `dictation_pipeline` work without modification.

The migration is idempotent — running it twice doesn't double-store
or re-migrate already-migrated keys (verified by
`test_migrate_is_idempotent`).

## Security considerations

- **Threat model**: a co-located user on a multi-user POSIX system, or
  an attacker who gains read access to the user's home directory
  (e.g. via a stolen laptop with an unencrypted home partition, or a
  cloud-synced `~/.config` folder).`config.json` had
  `0o600` perms but was still plaintext — a backup or snapshot would
  leak all API keys. ensures the plaintext is only in the
  keychain, which is encrypted at rest by the OS.

- **What does NOT protect against**: an attacker with arbitrary
  code execution as the user. The keyring is unlocked when the user
  is logged in, so any process running as the user can read the
  secrets via `keyring.get_password`. This is the same threat model
  as the plaintext `config.json` — no regression.

- **Keychain lock / unlock**: on macOS and Linux (with
  `gnome-keyring-daemon`), the keychain is unlocked at login. If the
  user locks the keychain manually (e.g. `gnome-keyring-daemon -l`),
  `keyring.get_password` will raise. `credential_store.load_secret`
  catches this and falls back to `None` (the secret is treated as
  "not configured"). The user sees an empty API key field in the
  renderer and can re-enter the key (which will be re-stored in the
  now-unlocked keychain).

- **Keychain wipe**: if the user wipes their keychain (e.g. resets
  GNOME Keyring), the `keyring://` reference tokens in `config.json`
  point to nothing. On the next `Config.load()`, the reference is
  resolved to `None` and the field is cleared. The user sees an empty
  API key field — they must re-enter the key. There is no automatic
  recovery (the secret is genuinely gone).

## See also

- `voice_typer/server/credential_store.py` — implementation.
- `voice_typer/server/config.py:Config.save()` — on-disk format with
  reference tokens.
- `voice_typer/server/config.py:Config.load()` — migration trigger and
  reference resolution.
- `voice_typer/server/service.py:apply_config()` — IPC `set_config`
  routing through `store_secret`.
- `voice_typer/server/service.py:get_config()` — `keyring_status`
  field in the IPC response.
- `voice_typer/client/src/renderer/src/components/common/KeyringStatusBadge.tsx`
  — renderer indicator component.
- `tests/test_credential_store.py` — unit tests.
