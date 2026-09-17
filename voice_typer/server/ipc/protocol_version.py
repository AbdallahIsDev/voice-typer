"""Single source of truth for the IPC protocol version.

Bump this integer when:
- ``_COMMAND_REGISTRY`` (in ``voice_typer/server/ipc_server.py``) adds,
  removes, or renames a command
- Push-event ``type`` vocabulary (in ``voice_typer/server/push_events.py``)
  changes

Cross-language parity: this MUST match:

- Rust ``EXPECTED_PROTOCOL_VERSION`` (in ``src-tauri/src/sidecar/ws.rs``)
- TypeScript ``IPC_PROTOCOL_VERSION`` (in
  ``voice_typer/client/src/renderer/src/types/ipc/push_events.ts``)

Parity is enforced by ``tests/test_ipc_protocol_cross_language_parity.py``.

DO NOT bump without coordinating across all three languages, a bump is
a deliberate, multi-file change. The version is monotonic and never
reused.

Historical note: this constant was previously duplicated as
``IPC_PROTOCOL_VERSION: int = 1`` in
``voice_typer/server/ipc/transport_tcp.py`` (deleted with the TCP
transport) and ``PROTOCOL_VERSION: int = 1`` in
``voice_typer/server/sidecar_ws.py``. The WS transport now imports
from this module so it cannot silently drift.
"""

PROTOCOL_VERSION: int = 1
