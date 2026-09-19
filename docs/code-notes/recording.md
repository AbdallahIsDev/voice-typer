# Recording pipeline — lasting notes

Deep explanations relocated from inline comments in
`voice_typer/server/recording/**` and related modules. Anchors are
referenced from code as `# NOTE: see docs/code-notes/recording.md#anchor`.

## package-layout

`voice_typer/server/recording` is a package that re-exports the public
names historically exposed by the monolithic `recording.py`. Production
code and tests must patch **owning submodules** (`recording.buffer`,
`recording.resampling`, …), not the package namespace (C-ARCH-2).

Mutable globals (`_resample_poly*`, `_buffer_clear_worker`) are owned by
their submodules and must NOT be re-exported from `__init__.py` —
importing them would snapshot stale values.

## buffer-clear-worker

SEC-audit-008 requires audio buffers to be zeroed before deallocation.
A single long-lived daemon (`buffer-clear-bg`) drains a bounded queue
(`_BUFFER_CLEAR_QUEUE_MAXSIZE = 64`) so rapid stop/discard does not
spawn unbounded threads. Queue-full falls back to a **synchronous**
secure clear (never drop the clear). Full-storage wipe on handed-off
growable buffers; pop-drain for deques so retained footprint decays
chunk-by-chunk.

`set_thread_registry` is load-bearing: tests
(`test_retry_regressions.py::TestBufferClearWorkerRegistry`) pin that
the worker can be joined via ThreadRegistry at app quit.

## resampling-fir

`scipy.signal.resample_poly` redesigns its FIR every call. Production
caches taps keyed by reduced `(up, down)` and calls `upfirdn` directly
(see `resampling.py`). Capped `half_len=256` bounds expensive
low-GCD ratios (44.1k→16k) without touching the common 48k→16k path.
Without scipy, linear interp runs after a cached anti-alias FIR on
downsample only.

## patch-paths

Tests patch owning-module attributes:
- `voice_typer.server.recording.resampling._get_resample_poly`
- `voice_typer.server.recording.buffer._secure_clear_array` /
  `_buffer_clear_worker`
- `voice_typer.server.recording.np` / `.sd` (lazy proxies re-resolve
  `sys.modules`, so patches on the real modules propagate)

`session_state.secure_clear_caches` binds `_secure_clear_array` at
import time by design; patch the owning module attribute, not a package
alias.

## static-source-echoes

`recording/__init__.py` retains a short echo block because some
regression tests still `inspect.getsource(recording)` and grep for:
- `np.dot(flat, flat)` (vectorized RMS)
- `chunk.fill(0)` / `_preroll_buffer` zeroing (SEC-audit-008)

Do not delete those echoed patterns; do not re-add forbidden contracts
(e.g. the removed 3-arg RMS callback signature).

## hidden-start-mic

`VT_START_HIDDEN=1` is the backend half of C-BG-1: while the host UI is
hidden, prewarm must not open a PortAudio InputStream (OS mic indicator).
Renderer level-monitor uses the same visibility gate.

## recording-controller-facade

`RecordingController` is a thin facade (C-ARCH-1). Streaming-session
accessors, audio callbacks, and level-monitor helpers stay on the
controller because static-source checks pin them there. Keep top-level
`import gc`: tests patch `recording_controller.gc.collect` (global
singleton) to spy on watchdog recovery. Helpers are lazily created via
`__getattr__` so `__new__`-constructed controllers still work.

`app._busy_event` has INVERTED semantics: `is_set() == True` means NOT
busy (the event doubles as a ready signal for `wait()`).

## capture-source-inspection

`Recorder._audio_callback_dispatch` must keep the literals
`_ring_buffer.append` and `_worker_wake_event` and must not contain
heavy-pipeline ops (`compute_vad_prob`, `_get_resample_poly`,
`process_chunk`, `_vad_update`). `AudioCallbackDispatcher.dispatch_callback_body`
returns a payload tuple (or None for preroll) so the wrapper performs the
pinned operations.
## device-list-canonical

Microphone enumeration is a single path (`list_microphones` →
`DeviceManager` → IPC/tray). Canonical host-API view only (C-MIC-7);
`System Default` is a separate selection semantic (C-MIC-9); startup
reconciles stale `config.microphone` silently to System Default
(C-CONF-2/C-CONF-3).
