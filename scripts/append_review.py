#!/usr/bin/env python3
"""Append IN- prefixed findings to review.md for Group 2 (Performance & Resources)."""

import os

REVIEW_PATH = "/home/z/my-project/voice-typer/review.md"

ENTRIES = []


def add(num, title, status, desc, impact, root_cause, progress, files, fix, severity):
    ENTRIES.append(f"""
### IN-{num} — {title}
**Status:** {status}
**Description:** {desc}
**User Impact:** {impact}
**Root Cause:** {root_cause}
**Progress:** {progress}
**Related Files:**
{files}
**Fix:** {fix}
**Severity:** {severity}
""")


# === Agent 1: Audio recording pipeline ===
add(
    1,
    "recorder.py current_duration_seconds O(chunks) per poll at 4Hz",
    "❌ Not Fixed",
    "The streaming thread polls `current_duration_seconds` at 4 Hz as an early-exit guard. Each call iterates ALL "
    "chunks in the buffer via `sum(int(c.shape[0]) for c in buffer)` — O(N) Python-level loop. For a 30-min 16kHz "
    "recording (~28,800 chunks), this costs ~7ms per call × 4Hz = ~28ms/sec (~3% of one core) sustained throughout "
    "recording.",
    "During long dictation sessions, the app consumes ~3% CPU just to compute a polling guard. On battery-powered "
    "laptops this prevents deep C-states, draining battery faster. The cost grows linearly with recording length.",
    "No running counter is maintained; each poll re-iterates the deque.",
    "None yet.",
    "- `voice_typer/server/recording/recorder.py:2355-2372`",
    "Maintain a running `_total_buffered_samples: int` counter incremented in `audio_pipeline.append_to_buffer_locked`."
    "Then `current_duration_seconds` becomes `self._total_buffered_samples / sr` — O(1). Reset in `reset_session_state`"
    "and `stop()`/`discard()`.",
    "🟡 Medium",
)

add(
    2,
    "_recorder_split.py stop_recording 230MB transient allocation",
    "❌ Not Fixed",
    "`stop_recording` stats computation does 3 separate O(N) passes over the full audio array, 2 creating full-size "
    "intermediates: `np.abs(flat).max()` (~115MB) and `np.sum(np.abs(audio) < 0.001)` (~115MB abs + ~29MB bool). For a "
    "30-min recording, peak transient is ~374MB, compounding existing ER-19's ~573MB peak.",
    "On memory-constrained devices (4GB tablets, Raspberry Pi), stopping a long recording can trigger GC pressure or "
    "OOM. The per-chunk path already uses the allocation-free pattern — the stop path is inconsistent.",
    "Copy-paste from older version; the per-chunk optimization was never back-ported to stop().",
    "None yet.",
    "- `voice_typer/server/recording/_recorder_split.py:930-937`",
    "Replace `np.abs(flat).max()` with `max(float(flat.max()), -float(flat.min()))` (0 extra memory). Compute "
    "`np.abs(flat)` ONCE and reuse for both peak and silence_pct. Reduces transient from ~230MB to ~115MB.",
    "🟡 Medium",
)

# === Agent 2: Config + app ===
add(
    3,
    "app.py lazy property retry causes 94Hz log spam + AttributeError on hot path",
    "❌ Not Fixed",
    "Six lazy @property accessors cache None on failure (`except Exception: log.warning(...); return None`), so every "
    "subsequent access re-enters the try block. The `audio_quality` property is on a HOT PATH (~94 calls/sec at "
    "48kHz/512). If AudioQualityController construction fails, every chunk crashes with AttributeError + logs a WARNING"
    "— 94 crashes/sec + 94 logs/sec during recording.",
    "If audio quality controller construction ever fails, recording becomes unusable with 94 crashes per second. Even "
    "for non-hot-path properties, WARNING log spam floods the log file on every dictation cycle until a fallback is "
    "assigned.",
    "The except branch does not cache a failure sentinel, so every access re-attempts construction.",
    "None yet.",
    "- `voice_typer/server/app.py:587-749`",
    "Cache a failure sentinel (e.g. `_LAZY_FAILED`) for a bounded TTL. For the hot-path `audio_quality` property, "
    "construct eagerly in __init__ or catch AttributeError in the delegate.",
    "🟡 Medium",
)

# === Agent 3: Model management ===
add(
    4,
    "transcription.py Whisper lock held during entire segment loop — blocks watchdog",
    "❌ Not Fixed",
    "`TranscriptionEngine.transcribe()` acquires `self._lock` (RLock) and holds it for the ENTIRE "
    "`_transcribe_unlocked` call — including the segment loop that drives ctranslate2 decoding (0.5-3s per segment). "
    "For a 5-min dictation with 100+ segments, lock hold is 10-30s. Parakeet's transcribe() already fixed this "
    "(releases lock during inference via `_active_inference` counter + `_inference_cond`); Whisper never got the same "
    "fix.",
    "The watchdog's `force_unload_active()` cannot tear down a stuck Whisper backend in bounded time because `unload()`"
    "blocks on the lock held by the stuck transcription. The 3-min force-recover timeout is meaningless if unload "
    "itself is blocked. Concurrent transcribes serialize.",
    "Whisper's transcribe() was never updated to match Parakeet's lock-release-during-inference pattern.",
    "None yet.",
    "- `voice_typer/server/transcription.py:888-889`",
    "Add `_active_inference` counter + `_inference_cond = threading.Condition(self._lock)`. In transcribe(), acquire "
    "lock only to check model + increment counter, release before segment loop. In unload(), wait on cond for "
    "counter==0.",
    "🔴 High",
)

add(
    5,
    "parakeet_engine.py batch size frozen at import time",
    "❌ Not Fixed",
    "`_INFERENCE_BATCH_SIZE` is a CLASS-level attribute evaluated at module import: `max(1, "
    "int(os.environ.get('PARAKEET_BATCH_SIZE', '1')))`. Setting the env var after app start has no effect — the "
    "operator must restart the entire app to change batch size.",
    "Operators tuning batch size for their GPU must restart the app. The env-var tuning knob is effectively a startup "
    "flag, not a runtime config. No way to change per-session.",
    "Expression runs at class body evaluation time (module import), not per-instance.",
    "None yet.",
    "- `voice_typer/server/parakeet_engine.py:875`",
    "Move to instance attribute read in __init__, or expose as Config field (parakeet_batch_size) so it can be changed "
    "via Settings.",
    "🟡 Medium",
)

add(
    7,
    "model_manager.py set_active_backend TOCTOU — can unload model mid-transcription",
    "❌ Not Fixed",
    "`set_active_backend` checks `recorder.recording` and `_busy_event` OUTSIDE the lock, then the background thread "
    "acquires `_model_change_lock` + `_config_mutation_lock` but does NOT re-check recording/busy inside those locks. "
    "The comment claims 'the background thread re-checks under the lock for race-safety' — this is FALSE. A recording "
    "can start between the check and the unload.",
    "User presses F2 (start recording) while set_active_backend IPC is in flight. The background thread unloads the old"
    "backend's model mid-transcription → use-after-free, ctranslate2 heap corruption (0xC0000374), or stuck thread. "
    "This is the exact crash class the deferral pattern was designed to prevent.",
    "Recording/busy state is mutated under _watchdog_lock, NOT under _model_change_lock. The check is a TOCTOU race.",
    "None yet.",
    "- `voice_typer/server/model_manager.py:1069-1249`",
    "Re-check recorder.recording and _busy_event INSIDE _model_change_lock, AND acquire _watchdog_lock for the check so"
    "recording-start and unload are mutually exclusive. Correct the misleading comment.",
    "🟡 Medium",
)

# === Agent 4: IPC ===
add(
    9,
    "transport_tcp.py worker pool starvation — connections and dispatch share max_workers=4",
    "❌ Not Fixed",
    "A single ThreadPoolExecutor(max_workers=4) handles BOTH connection read-loops (blocking, occupy a worker for the "
    "entire connection lifetime) AND dispatch submissions. With 4 concurrent connections, all workers are stuck in read"
    "loops and dispatch queues indefinitely — dispatch is completely starved.",
    "Under reconnect storms or multi-window Electron (4+ connections), all IPC dispatch deadlocks. Even get_status "
    "cannot run. The pool's unbounded queue grows with every queued dispatch.",
    "Single shared pool for two qualitatively different work types (long-lived blocking reads vs short-lived "
    "dispatches).",
    "None yet.",
    "- `voice_typer/server/ipc/transport_tcp.py:91-94, 226, 683`",
    "Use SEPARATE pools: _tcp_conn_pool (max_workers=8, for read-loops) and _tcp_dispatch_pool (max_workers=4, for "
    "dispatch). Or increase max_workers to 16.",
    "🔴 High",
)

add(
    10,
    "sender.py double UTF-8 encoding of every outbound TCP frame",
    "❌ Not Fixed",
    "Every outbound frame is UTF-8 encoded TWICE: once at sender.py:206 `len(line.encode('utf-8'))` to check size "
    "(bytes discarded), then again at transport.py:112 `_write_buffer.append(text.encode('utf-8'))`. For a 100KB "
    "transcription_final event, that's 200KB of encoding work per push.",
    "~2x UTF-8 encoding CPU on every outbound frame; doubled allocation pressure for large payloads. At 15-50Hz "
    "bubble_level rate, overhead accumulates.",
    "_send checks size via encode but passes str to _TCPLineIO.write which re-encodes.",
    "None yet.",
    "- `voice_typer/server/ipc/sender.py:206, 404`",
    "Encode once: `line_bytes = (line + '\\n').encode('utf-8')`; check len; add write_bytes() to _TCPLineIO that "
    "appends pre-encoded bytes.",
    "🟡 Medium",
)

add(
    11,
    "sender.py _pending_tcp uses O(N) del list[:n] on every push while disconnected",
    "❌ Not Fixed",
    "`del self._pending_tcp[:dropped]` is O(N) — shifts all remaining elements left. While client is disconnected, "
    "_send appends + caps on every push (15-50Hz bubble_level), so this O(N) shift runs 15-50 times/sec with N up to "
    "1000.",
    "At 50Hz push rate while disconnected, ~50,000 element-shifts/sec of pure overhead on the audio/callback thread.",
    "list front-deletion is O(N); deque(maxlen=N) popleft is O(1).",
    "None yet.",
    "- `voice_typer/server/ipc/sender.py:576-578`",
    "Change _pending_tcp from list[str] to collections.deque[str](maxlen=_TCP_PENDING_BUFFER_CAP). Auto-evicts from "
    "left in O(1).",
    "🟡 Medium",
)

add(
    12,
    "transport.py readline char/byte cap 4x too loose for non-ASCII",
    "❌ Not Fixed",
    "`_max_line_chars = _max_line_bytes` (1MB) but readline(n) limits CHARS not bytes. UTF-8 worst case is 4 "
    "bytes/char, so 1M chars = 4MB. A client sending 1M-char emoji lines forces 4MB buffer. The stated 1MB cap is "
    "effectively 1-4MB.",
    "A local attacker can send 4x the intended max line size, inflating per-connection memory by 4x. With 4 concurrent "
    "connections, 16MB instead of 4MB.",
    "Char/byte conflation. readline(size) in text mode limits chars, not bytes.",
    "None yet.",
    "- `voice_typer/server/ipc/transport.py:185-194`",
    "Read in binary mode and apply byte cap directly, or set _max_line_chars = _max_line_bytes // 4 (true worst case).",
    "🟡 Medium",
)

# === Agent 5: History + text cleanup ===
add(
    13,
    "history_db writer.py _close_worker crashes on _BatchableInsert during shutdown",
    "❌ Not Fixed",
    "`_close_writer`'s queue.Full drain loop does `dropped_fn, dropped_future = dropped` (line 695) assuming a 2-tuple",
    "but `_BatchableInsert` uses __slots__ with no __iter__, so this raises TypeError. The sibling "
    "`_drop_oldest_for_overflow` correctly handles both shapes. Triggers in the exact stalled-writer scenario PERF-5 "
    "was designed for: writer thread stalled → queue fills with _BatchableInsert items → close() → TypeError propagates"
    "uncaught → shutdown fails.",
    "When the writer thread stalls (disk full, antivirus WAL lock), calling close() crashes with TypeError instead of "
    "gracefully draining. The shutdown sentinel is never enqueued, the writer daemon never sees it, and the 10s join "
    "timeout is the only thing that lets close() return.",
    "_close_writer's drain loop was written before _BatchableInsert was introduced and never updated to handle the "
    "structured payload shape.",
    "None yet.",
    "- `voice_typer/server/history_db_internals/writer.py:695`",
    "Mirror _drop_oldest_for_overflow's shape check: `if isinstance(dropped, _BatchableInsert): dropped_future = "
    "dropped.future else: _, dropped_future = dropped`. Widen except to (RuntimeError, TypeError).",
    "🔴 High",
)

add(
    14,
    "text_cleanup.py _token_key recomputed ~4N times per dictation — no memoization",
    "❌ Not Fixed",
    "`_token_key(token)` (regex sub + lower) is called ~4N times across 4 token helpers for N tokens. "
    "`_duplicate_phrase_length` alone recomputes each token's key up to 8x as the window slides. For 1000-token "
    "dictation, ~4000 regex subs; English ~50% token repetition means ~50% are redundant.",
    "~4N regex subs per dictation on the transcription hot path. For 30 dictations/min × 1000 tokens = 120,000 regex "
    "subs/min of pure waste.",
    "_token_key is a pure function of token but is neither memoized nor precomputed once per dictation.",
    "None yet.",
    "- `voice_typer/server/text_cleanup.py:700-807`",
    "Use functools.lru_cache(maxsize=4096) on _token_key, or precompute keys list once in clean_transcribed_text and "
    "pass through helpers.",
    "🟡 Medium",
)

add(
    15,
    "history_db.py OFFSET-based pagination O(offset) for deep pages",
    "❌ Not Fixed",
    "get_recent/search/get_favorites use `LIMIT ? OFFSET ?`. SQLite must scan and discard the first `offset` rows. For "
    "50K-row DB at page 200 (offset=10000), that's 10,000 index entries scanned + 10,000 row lookups every page fetch.",
    "History UI pagination latency grows linearly with page depth. At 50K rows, page 200 is ~200ms; page 1000 is ~1s. "
    "Users scrolling deep into history see increasing lag.",
    "No cursor-based (keyset) pagination path exists.",
    "None yet.",
    "- `voice_typer/server/history_db.py:1996-2154`",
    "Add optional before_timestamp/before_id parameters; use `WHERE timestamp < ? AND id < ?` instead of OFFSET. Keep "
    "OFFSET as fallback.",
    "🟡 Medium",
)

add(
    16,
    "history_db.py no upper bound on limit parameter",
    "❌ Not Fixed",
    "limit is passed straight to SQL LIMIT ? with no clamp. cursor.fetchall() materializes every row. A malformed IPC "
    "request passing limit=10**6 would attempt to materialize ~600MB (1M rows × ~600 bytes with 500-char preview).",
    "Worst-case memory spike + ~600MB allocation + 1M dict copies. Could OOM a 32-bit process. Today's callers pass "
    "sensible defaults (50) but it's an unguarded surface.",
    "Defensive bounds exist for query input but not for response size.",
    "None yet.",
    "- `voice_typer/server/history_db.py:1960-2154`",
    "Add _MAX_LIST_LIMIT = 500; clamp `limit = min(max(limit, 1), _MAX_LIST_LIMIT)` at top of each method.",
    "🟡 Medium",
)

# === Agent 6: Shutdown + recording controller ===
add(
    17,
    "shutdown_controller.py join_leaked_workers never called before os._exit",
    "❌ Not Fixed",
    "`_run_with_timeout` appends timed-out daemon threads to `_LEAKED_WORKERS`. `join_leaked_workers` exists to drain "
    "this registry, but it is NEVER IMPORTED into shutdown_controller.py. The `_watchdog` function calls `os._exit(0)` "
    "directly without joining leaked workers. The test `test_watchdog_calls_join_leaked_workers` expects the call but "
    "the implementation is missing.",
    "Every _run_with_timeout timeout during shutdown leaks a daemon thread holding PortAudio/SQLite/file-handle "
    "resources. On rapid restart cycles, leaked threads' resources may not be released before the next process claims "
    "them.",
    "The SU-26 fix was intended but never implemented. The import and call are both missing.",
    "None yet.",
    "- `voice_typer/server/shutdown_controller.py:1485-1531, 74-79`",
    "Add join_leaked_workers to the import. In _watchdog, call `join_leaked_workers(timeout=0.5)` BEFORE "
    "`os._exit(0)`.",
    "🔴 High",
)

add(
    18,
    "recording_controller.py _cancelled_cycle_ids unbounded growth — comment claims discard exists",
    "❌ Not Fixed",
    "`_cancelled_cycle_ids: set[str]` is declared with a comment claiming 'Entries are discarded by the pipeline's "
    "finally block to keep the set bounded.' This is FALSE — grep confirms NO discard/remove/pop/clear calls exist "
    "anywhere. Entries are only added (on ESC cancel and watchdog force-recover). The set grows by one entry per cancel"
    "event, forever.",
    "Unbounded memory growth in a long-running tray app. A user who frequently cancels stuck transcriptions accumulates"
    "one entry per cancel. After 100,000 cancel events (~20 bytes each), ~2MB of retained strings.",
    "The discard logic documented in the comment was never implemented.",
    "None yet.",
    "- `voice_typer/server/recording_controller.py:130, 1192-1193, 1641-1642`",
    "In dictation_pipeline's finally block, discard the current cycle_id from _cancelled_cycle_ids under lock. Or use a"
    "bounded OrderedDict (LRU eviction at 1000 entries).",
    "🔴 High",
)

add(
    19,
    "shutdown_controller.py _teardown_asr_models races _teardown_recorder — concurrent model unload",
    "❌ Not Fixed",
    "All teardown helpers run concurrently in the same _run_parallel_with_timeout batch. `_teardown_asr_models` (placed"
    "FIRST) calls `registry.unload()` which tears down the ctranslate2 model, while `_teardown_recorder` may still have"
    "a leaked transcription thread accessing that model. ctranslate2 is not thread-safe for concurrent calls on the "
    "same model.",
    "Use-after-free / concurrent model access → crash or silent corruption during shutdown. On GPU systems, CUDA "
    "context teardown racing an in-flight inference call can leave the GPU in an inconsistent state for the next "
    "process launch.",
    "_teardown_asr_models is placed FIRST but has no dependency on _teardown_recorder completing first.",
    "None yet.",
    "- `voice_typer/server/shutdown_controller.py:403-419`",
    "Move _teardown_asr_models to a SECOND wave after _teardown_recorder and _teardown_timers_and_recording drain. Or "
    "set a force-abort flag before unload().",
    "🔴 High",
)

add(
    20,
    "recording_controller.py _toggle_lock held during 5-30s model reload — ESC unresponsive",
    "❌ Not Fixed",
    "`start()` holds `_toggle_lock` for the entire `_start_impl` body, including `ensure_active_engine_loaded()` which "
    "can block 5-30s when the idle-unload timer had fired. During this window, any cancel() (ESC hotkey) blocks on "
    "_toggle_lock. The tray shows RECORDING but ESC is unresponsive for up to 30s.",
    "ESC-to-cancel is unresponsive for 5-30s after starting a recording when the model needs reloading. The user cannot"
    "abort a recording that hasn't finished loading its model.",
    "The fix that moved ensure_active_engine_loaded to AFTER recorder.start() solved the 'lost first 5-30s of speech' "
    "problem but did not address the _toggle_lock contention.",
    "None yet.",
    "- `voice_typer/server/recording_controller.py:441-651`",
    "Release _toggle_lock before calling ensure_active_engine_loaded(). Re-acquire after. Or make "
    "ensure_active_engine_loaded non-blocking by deferring to the transcription thread.",
    "🟡 Medium",
)

# === Agent 7: Clipboard + cred + security ===
add(
    21,
    "security.py redact_pii missing fast-path that _redact_text has",
    "❌ Not Fixed",
    "`_redact_text` (the logging.Filter path) has a fast-path: `if not _FAST_TRIGGER.search(text): return text`. The "
    "standalone `redact_pii()` helper does NOT — it always runs 8-12 regex subs. `redact_pii` is the public API used by"
    "transcription.py:919 which logs transcription text per segment.",
    "Per-segment transcription logging with log_transcriptions=True pays 8-12 re.sub calls per segment, ~50-100ms of "
    "regex work per dictation.",
    "Fast-path was added to _redact_text only; the standalone redact_pii helper was not updated.",
    "None yet.",
    "- `voice_typer/server/security.py:239-300`",
    "Add the same `if not _FAST_TRIGGER.search(text): return text` guard at the top of redact_pii. Or make redact_pii "
    "delegate to _redact_text.",
    "🟡 Medium",
)

add(
    22,
    "clipboard/manager.py _release_stuck_modifiers runs before rate-limit/paste_enabled gates",
    "❌ Not Fixed",
    "`_release_stuck_modifiers()` unconditionally invokes 4 pynput .release() calls (ctrl, shift, alt, cmd) — each a "
    "per-key OS round-trip. It runs BEFORE the rate-limit check (line 842) and paste_enabled check (line 852). A "
    "rate-limited or disabled paste still pays 4 OS keystroke-synthesis syscalls.",
    "Rapid dictation attempts (<500ms apart) are rate-limited but still pay 4 pynput syscalls each. On Linux X11, 4× "
    "XTestFakeKeyEvent IPC round-trips (~1-2ms each = 4-8ms wasted per rate-limited paste).",
    "_release_stuck_modifiers is positioned BEFORE the gates.",
    "None yet.",
    "- `voice_typer/server/clipboard/manager.py:817, 842, 852`",
    "Move _release_stuck_modifiers() AFTER both the rate-limit and paste_enabled checks, just before "
    "_is_safe_paste_target().",
    "🟡 Medium",
)

add(
    23,
    "credential_store.py orphaned keyring threads accumulate unbounded",
    "❌ Not Fixed",
    "`_run_keyring_call` spawns a fresh daemon Thread per keyring call. On timeout (5s), the thread is orphaned (Python"
    "can't kill threads). The docstring acknowledges 'the orphaned thread keeps running.' At startup, Config.load() "
    "calls load_secret() for 5 providers. If keyring is hung (broken D-Bus), 5 orphaned threads accumulate. Each "
    "subsequent IPC set_config adds another.",
    "A user with a broken D-Bus (common in headless/WSL/container) accumulates ~1 orphaned thread per minute of use. "
    "Each holds ~1MB stack. After 1 hour of heavy use, ~60MB of address space consumed by orphaned threads that are "
    "never reclaimed.",
    "Intentional one-thread-per-call design to avoid pooling deadlock, but orphan accumulation is unbounded.",
    "None yet.",
    "- `voice_typer/server/credential_store.py:126-163`",
    "Track orphaned-thread count; log WARNING when >20. On second consecutive timeout, set a 'backend is wedged' flag "
    "that short-circuits for a 60s cooldown.",
    "🟡 Medium",
)

# === Agent 8: Hotkeys ===
add(
    24,
    "wayland.py socket collision — 3 backends bind same socket path, 2 orphaned",
    "❌ Not Fixed",
    "HotkeyDispatcher creates 3 independent WaylandHotkey backends (dictation, ESC, repaste). All 3 try to bind the "
    "SAME hardcoded path '$XDG_RUNTIME_DIR/voice-typer-hotkey.sock'. The 2nd and 3rd os.unlink() the previous socket "
    "and rebind. The 1st and 2nd instances' _accept_loop threads keep listening on orphaned socket FDs — no client can "
    "reach them.",
    "On Linux Wayland (without native evdev binary), external tools can only reach the LAST-registered backend "
    "(repaste). A 'toggle' command silently invokes the repaste callback instead of toggling dictation. Dictation and "
    "ESC are unreachable via socket. Plus 2 orphaned accept-loop threads + 2 orphaned sockets + 2 spurious pynput "
    "listeners.",
    "SOCKET_PATH is a class-level constant with no per-backend suffix; no collision detection on bind().",
    "None yet.",
    "- `voice_typer/server/hotkeys/wayland.py:75-77, 355-390`",
    "Make SOCKET_PATH per-instance: append role suffix (dictation/esc/repaste). Or route all 3 hotkeys through ONE "
    "WaylandHotkey instance that multiplexes commands.",
    "🔴 Critical",
)

add(
    25,
    "windows_native.py LL hook proc runs callback inline — system-wide keyboard freeze risk",
    "❌ Not Fixed",
    "The WH_KEYBOARD_LL hook proc runs on the message-pump thread and calls `callback()` INLINE (line 800). Win32 "
    "imposes LowLevelHooksTimeout (default 300ms); if the proc doesn't return within that window, Windows silently "
    "disables the hook. callback() is the dispatcher's _dictation_callback which calls _start_dictation() "
    "(recorder.start, a 610-line init).",
    "System-wide keyboard input latency spikes on every hotkey press. Risk of Windows silently removing the LL hook if "
    "callback exceeds 300ms — the exact 'Escape does nothing' regression the LL hook was added to fix.",
    "The hook proc performs application work inline rather than dispatching to a worker thread.",
    "None yet.",
    "- `voice_typer/server/hotkeys/windows_native.py:780-824`",
    "Dispatch callback() to a dedicated worker thread from the hook proc. The hook proc must return within ~1ms.",
    "🔴 High",
)

add(
    26,
    "windows_native.py modifier-only hotkeys forced to 125Hz polling — sustained 500-5000 syscalls/sec",
    "❌ Not Fixed",
    "Modifier-only hotkeys (e.g. <alt>, <ctrl>) are explicitly excluded from the LL hook path by `not "
    "self._is_modifier_only`. They ALWAYS fall through to `_run_modifier_only_polling_loop` which spins at 8ms cadence "
    "(~125Hz) for the ENTIRE app lifetime. Per-iteration: 3-5 syscalls idle, up to 14 syscalls when held.",
    "Sustained ~500-5000 syscalls/sec on Windows whenever a modifier-only hotkey is configured. Battery drain on "
    "laptops (prevents deep C-states). Asymmetric perf: modifier-only specs get fundamentally worse experience than "
    "key+modifier specs.",
    "The LL hook proc checks `vk == backend._vk` but _vk is None for modifier-only specs, so the hook can't match. The "
    "polling loop is the only path.",
    "None yet.",
    "- `voice_typer/server/hotkeys/windows_native.py:290, 311, 491-493, 911-1215`",
    "Extend _hook_proc to match modifier VKs when _vk is None. Drop the `not self._is_modifier_only` guard on "
    "simple_key so modifier-only specs use the LL hook.",
    "🔴 High",
)

add(
    27,
    "native_adapter.py dual-backend running — legacy not stopped when native restarts",
    "❌ Not Fixed",
    "When state is FALLBACK (legacy running) and the 60s permission timer fires, `_on_permission_granted` restarts the "
    "native backend and sets state=NATIVE — but NEVER calls `self._legacy.stop()`. Both backends now match the same "
    "hotkey; BOTH fire the callback on the next keypress.",
    "DOUBLE-FIRE: dictation hotkey pressed → both backends fire toggle_dictation → toggle + untoggle = no-op (user sees"
    "nothing happen). Resource leak: legacy backend's listener thread + OS resources remain allocated indefinitely. On "
    "Windows, legacy may still hold a WH_KEYBOARD_LL hook.",
    "The state transition table explicitly documents 'NOTE: legacy NOT stopped when transitioning from FALLBACK' but "
    "doesn't justify why.",
    "None yet.",
    "- `voice_typer/server/hotkeys/native_adapter.py:306-337`",
    "In _on_permission_granted, before setting state=NATIVE, stop the legacy backend: `self._legacy.stop(); "
    "self._legacy = None`.",
    "🟡 Medium",
)

# === Agent 9: Service model ===
add(
    28,
    "service/model.py download_model no finally — Event leak on failed download",
    "❌ Not Fixed",
    "`_download_whisper_family` has NO finally block. If poll_download_progress raises, the cleanup at line 1049 is "
    "skipped, the per-download threading.Event is leaked in `_download_cancel_events` forever. The outer "
    "`download_model`'s `if download_id is not None: self._unregister_download(download_id)` is DEAD CODE — the outer "
    "download_id is always None (Python doesn't propagate assignments across function scopes).",
    "`_download_cancel_events` dict grows by one entry per failed download — unbounded memory growth. The dead-code "
    "unregister gives a false sense of safety. `_active_download_id` may stay pointing at the leaked download, breaking"
    "cancel_model_download for subsequent downloads.",
    "Python does not propagate variable assignments across function scopes. The outer handler's check is always False.",
    "None yet.",
    "- `voice_typer/server/service/model.py:806, 836-837, 923, 994, 1049, 1071`",
    "Remove dead outer-handler code. Add `finally:` block in _download_whisper_family that calls _unregister_download "
    "if download_id is not None.",
    "🔴 High",
)

add(
    29,
    "service/model.py deps probe find_spec re-runs every 5s forever",
    "❌ Not Fixed",
    "`_check_qwen_deps` / `_check_parakeet_deps` call `importlib.util.find_spec()` on every 5s cache miss. find_spec "
    "walks sys.path (filesystem stat per path entry). Package install state doesn't change while Voice Typer is "
    "running.",
    "Persistent low-level CPU + syscall overhead (2 × sys.path walk every 5s, forever). On systems with large sys.path "
    "(conda, many venvs), each find_spec is 10-50 stats.",
    "Deps probe result is cached alongside on-disk model status (5s TTL) but the result is invariant at runtime.",
    "None yet.",
    "- `voice_typer/server/service/model.py:228, 240, 421-457`",
    "Cache deps probe results separately with longer TTL (300s) or cache-on-first-call. Decouple deps-probe TTL from "
    "on-disk-status TTL.",
    "🟡 Medium",
)

add(
    30,
    "service/_download_helpers.py poll loop has NO timeout — hung download blocks IPC forever",
    "❌ Not Fixed",
    "The polling loop `while thread.is_alive(): thread.join(timeout=1.0); ...` has NO overall timeout. If the "
    "HuggingFace download thread hangs, the loop spins forever, doing a full rglob + per-file stat every 1s. The "
    "download_model IPC call never returns, blocking its executor thread permanently.",
    "A single hung download permanently consumes one IPC executor thread + 1 rglob/sec CPU. If the user retries, "
    "multiple hung downloads accumulate. No escape hatch except app restart.",
    "No max-duration or max-stall guard.",
    "None yet.",
    "- `voice_typer/server/service/_download_helpers.py:209, 258, 262`",
    "Add max-duration guard (30 min) or max-stall guard (if total_bytes_seen unchanged for N iterations, treat as "
    "stalled). Log + push download_stalled event on timeout.",
    "🟡 Medium",
)

# === Agent 10: Service vocab/template/privacy ===
add(
    31,
    "service/vocabulary.py get_vocabulary constructs new VocabularyManager per IPC call",
    "❌ Not Fixed",
    "`get_vocabulary` creates a fresh `VocabularyManager(config_dir=...)` on every IPC call. __init__ unconditionally "
    "calls `_load_and_merge()` which reads bundled corrections.json + user vocabulary file from disk + JSON-parses both"
    "+ merges. The live `self._app._vocabulary_manager` already holds the identical merged data in memory.",
    "5-50ms of redundant disk I/O per Vocabulary page load. The bundled corrections file (a packaged resource) is "
    "re-read on every page mount. On slow disks the latency is worse.",
    "The live manager exists but the mixin ignores it for reads (uses it only for writes).",
    "None yet.",
    "- `voice_typer/server/service/vocabulary.py:38-43`",
    "Reuse `getattr(self._app, '_vocabulary_manager', None)`. Return a deep copy of its data to prevent renderer "
    "mutation.",
    "🔴 High",
)

add(
    32,
    "service/template.py save_templates bypasses lock + doesn't rebuild indexes",
    "❌ Not Fixed",
    "`save_templates` does `tm._templates = normalized; tm._save()` WITHOUT acquiring `tm._lock` and WITHOUT calling "
    "`tm._rebuild_indexes()`. This races with concurrent `tm.match()` (called by dictation_pipeline on every dictation "
    "cycle) which reads _exact_index/_contains_list under the lock. The indexes still reference OLD template dicts.",
    "After every 'Save Templates' IPC call, dictation-pipeline match() runs against stale indexes — user adds a "
    "template, dictation still substitutes the OLD expansion (or none). Race window: small but real on a concurrent "
    "dictation cycle.",
    "The service-layer write path bypasses the TemplateManager's internal lock + index-rebuild contract.",
    "None yet.",
    "- `voice_typer/server/service/template.py:116-117`",
    "Add a public `TemplateManager.replace_all(normalized)` method that acquires _lock, swaps _templates, calls "
    "_rebuild_indexes(), calls _save().",
    "🔴 High",
)

add(
    33,
    "service/privacy.py GDPR delete doesn't invalidate in-memory vocabulary/templates",
    "❌ Not Fixed",
    "`delete_all_personal_data` unlinks vocabulary/templates/corrections files, then calls "
    "`_gdpr_invalidate_cached_engines` — but that only clears `_llm_polisher` and `_cloud_engine`. The live "
    "`_vocabulary_manager._data` and `_template_manager._templates` are NEVER invalidated. The dictation_pipeline keeps"
    "applying corrections/substitutions from the deleted user data until app restart.",
    "After GDPR Art. 17 erasure, the user's dictated text keeps getting vocabulary corrections + template substitutions"
    "applied from the deleted user files for the rest of the session. Direct Art. 17 violation — data is gone from disk"
    "but still actively used in memory.",
    "No reload/invalidation call between the unlink step and the post-cleanup sweep. Only llm_polisher/cloud_engine are"
    "nulled.",
    "None yet.",
    "- `voice_typer/server/service/privacy.py:702-711`",
    "Add _gdpr_invalidate_managers(app) that calls _vocabulary_manager._load_and_merge() and _template_manager._load() "
    "to re-read now-empty files.",
    "🔴 High",
)

# === Agent 11: Sidecar WS + onboarding + permissions ===
add(
    34,
    "sidecar_ws.py heartbeat fast-path bypasses rate limiter and shutdown gate",
    "❌ Not Fixed",
    "The WS heartbeat fast-path (`continue` at line 1113) skips `dispatch()` — and therefore skips the rate limiter, "
    "shutdown gate, and in-flight tracking. A buggy host whose heartbeat timer fires every 1ms sends 1000 "
    "heartbeats/sec; each does json.dumps + websocket.send inline on the event loop, consuming ~100% CPU.",
    "A buggy host can saturate the sidecar's event loop with heartbeats, starving real dispatches. During cooperative "
    "shutdown, heartbeats continue to be acked.",
    "Deliberate design to keep heartbeat-ack latency at ~1ms, but no defense-in-depth rate cap was added.",
    "None yet.",
    "- `voice_typer/server/sidecar_ws.py:1099-1113`",
    "Add a cheap heartbeat-specific rate cap (100-msg/10s) before the fast-path.",
    "🟡 Medium",
)

add(
    35,
    "sidecar_ws.py writer json.dumps + websocket.send block event loop",
    "❌ Not Fixed",
    "The _writer task does `json.dumps(event)` (CPU-bound, ~50-100ms for near-1MiB frame) then `websocket.send(raw)` "
    "(can block indefinitely on slow consumer) — both on the asyncio event loop thread. During json.dumps, no other "
    "coroutine runs including _read_loop (heartbeat acks delayed → host triggers FT-1 respawn).",
    "Under a burst of large outbound events, the sidecar becomes unresponsive to inbound frames. A slow host freezes "
    "the sidecar's read path.",
    "No off-loop serialization, no send timeout.",
    "None yet.",
    "- `voice_typer/server/sidecar_ws.py:980, 1014`",
    "Move json.dumps to worker thread via loop.run_in_executor. Wrap websocket.send in asyncio.wait_for(timeout=5.0).",
    "🟡 Medium",
)

add(
    36,
    "permissions.py check_permissions_payload is 104 lines of dead code with misleading docstring",
    "❌ Not Fixed",
    "`check_permissions_payload()` has ZERO callers in production or tests. Its docstring claims it's 'the canonical "
    "entry point for onboarding_check_permissions IPC handlers' — but the actual handler calls "
    "`OnboardingController().check_permissions()` (different function, different module, different i18n strategy: keys "
    "vs literal English strings).",
    "104 lines of dead code inflating permissions.py. Misleading docstring sends maintainers to the wrong function. "
    "Divergent i18n shapes (keys vs literals) — if someone wires it to a future handler, renderer receives unlocalized "
    "English. Also calls check_microphone_permission() (50-200ms Windows InputStream probe) that no one benefits from.",
    "The IPC handler was migrated to OnboardingController.check_permissions() but the dead function was never removed.",
    "None yet.",
    "- `voice_typer/server/permissions.py:343-447`",
    "Delete check_permissions_payload() and permission_probe_error_payload(). Update "
    "OnboardingController.check_permissions() docstring to declare it canonical.",
    "🟡 Medium",
)

# === Agent 12: Tray + misc ===
add(
    37,
    "tray_notifications.py CPU fallback not published to Tauri/Electron host",
    "❌ Not Fixed",
    "`on_parakeet_cpu_fallback` calls `tray._apply_state()` (updates pystray icon) but does NOT call "
    "`tray._publish_tray_state()` (pushes tray_state event to Tauri/Electron). The '(CPU fallback)' tooltip reaches the"
    "pystray tray icon but is never emitted as a tray_state event.",
    "On Linux/Tauri builds (where Electron UI is the primary surface and pystray may not be visible), the user never "
    "sees the '(CPU fallback)' indicator after a GPU→CPU fallback. They only observe transcription is slower, with no "
    "explanation.",
    "_apply_state updates pystray but does NOT call _publish_tray_state. The normal state-change path and elapsed-tick "
    "path both call _publish_tray_state.",
    "None yet.",
    "- `voice_typer/server/tray_notifications.py:129`",
    "After _apply_state, also call tray._publish_tray_state() (best-effort, in the same try/except).",
    "🔴 High",
)

add(
    38,
    "tray_elapsed_timer.py timer leak on rapid stop/restart — overlapping tick streams",
    "❌ Not Fixed",
    "`start()` calls `cancel()` then creates a new Timer, but cancel-then-start does not prevent overlapping timers "
    "when the prior tick is mid-execution. If T1 fires → enters slow callback → another thread starts() → creates T2 → "
    "T1's _tick finishes and reschedules creating T3, OVERWRITING _timer (T2 orphaned). After T2 and T3 both fire, two "
    "concurrent tick streams run for the remainder of the recording.",
    "2x (or N× for N rapid stop/restart cycles) tooltip updates per second, 2x IPC tray_state publishes, 2x pystray "
    "icon updates. Sustained for the entire recording session.",
    "cancel-then-start does not prevent overlapping timers when the prior tick is mid-execution. No generation "
    "counter.",
    "None yet.",
    "- `voice_typer/server/tray_elapsed_timer.py:96-140`",
    "Add a generation counter. start() increments _generation. _tick captures its generation and only reschedules if "
    "_generation matches.",
    "🟡 Medium",
)

add(
    39,
    "waveform_bubble_wiring.py getattr returns None instead of default for explicit-None fields",
    "❌ Not Fixed",
    "`_push_bubble_config` uses `getattr(cfg, name, default)` which returns default ONLY when the attribute is absent. "
    "If the attribute is present but explicitly None (e.g. cfg.custom_theme = None is the documented default for 'no "
    "custom theme'), getattr returns None, NOT the documented default. The bubble renderer receives null for "
    "theme_preset/bubble_behavior etc.",
    "Bubble renderer gets null instead of 'default'/'show_on_record'/'system', causing theme desync or broken bubble "
    "behavior on configs where these fields were never explicitly set (common after fresh install or config "
    "migration).",
    "getattr(cfg, name, default) returns default only when attr is absent, not when it's None.",
    "None yet.",
    "- `voice_typer/server/waveform_bubble_wiring.py:286-296`",
    "Use `getattr(cfg, name, None) or default` for non-None defaults. Keep getattr(cfg, 'custom_theme', None) where "
    "None is valid.",
    "🟡 Medium",
)

# === Agent 13: Level monitor ===
add(
    40,
    "level_monitor monitoring.py blocksize=512 hardcoded → 94Hz chunk rate, ring buffer holds 0.68s not 4s",
    "❌ Not Fixed",
    "The stream opens at device native rate (typically 44.1 or 48 kHz) but blocksize is hardcoded at 512. At 48kHz/512 "
    "= 93.75 Hz block rate (not 16 Hz). Ring buffer capacity 64 holds only 0.68s of audio — NOT the '~4s' the comment "
    "claims. With RNNoise enabled (~20ms/chunk), the worker can process ~50 chunks/sec but 94 arrive/sec → 44 "
    "chunks/sec deficit → buffer fills in ~1.5s → sustained drops → level bar freezes.",
    "Level bar freezes at 48 kHz with RNNoise enabled. 6× more indata.copy() allocations, 6× more worker wakeups, 6× "
    "more np.dot calls than at 16 kHz.",
    "blocksize doesn't scale with native_rate; capacity comment is stale (assumes 16 Hz).",
    "None yet.",
    "- `voice_typer/server/level_monitor/monitoring.py:409`",
    "Scale blocksize with sample rate: `blocksize = max(512, int(native_rate * 0.032))` (32ms blocks → ~31Hz). Or "
    "increase _LEVEL_RING_BUFFER_CAPACITY to 256.",
    "🔴 High",
)

add(
    41,
    "level_monitor worker.py update_level_processor doesn't acquire lock despite comment claiming it does",
    "❌ Not Fixed",
    "The comment in worker.py:385-387 claims `_level_processor is only mutated by update_level_processor (which "
    "acquires _monitor_lock)`. This is FALSE — `update_level_processor` does NOT acquire `_monitor_lock`. Furthermore, "
    "it reads `_monitor_sample_rate` without the lock while start_monitoring writes it under the lock.",
    "Future developer reads the comment, believes the lock is held, and introduces a real race. Stale "
    "_monitor_sample_rate → AudioProcessor tuned to wrong rate → degraded filter quality (highpass at wrong corner, "
    "RNNoise frame mismatch).",
    "Missing lock acquisition in update_level_processor; misleading comment in worker.py.",
    "None yet.",
    "- `voice_typer/server/level_monitor/worker.py:385-387`",
    "Acquire _monitor_lock in update_level_processor (snapshot _monitor_sample_rate + assign _level_processor under "
    "lock). Fix the comment.",
    "🟡 Medium",
)

add(
    42,
    "level_monitor worker.py join timeout clears thread slot → duplicate workers break SPSC",
    "❌ Not Fixed",
    "If `thread.join(timeout=1.0)` times out (worker stuck), the code unconditionally sets `_level_worker_thread = "
    "None`. The old thread is still alive. The next `_ensure_level_worker_running` sees None → spawns a NEW worker. Now"
    "two worker threads drain the same ring buffer concurrently. SPSC invariant broken → duplicate mic_level push "
    "events, duplicate test_raw_chunks appends.",
    "Under pathological worker stall, a second worker spawns and both process chunks → duplicate state writes, "
    "duplicate IPC events, corrupted test audio. Unrecoverable without process restart.",
    "Join timeout does not check thread.is_alive() before clearing the slot.",
    "None yet.",
    "- `voice_typer/server/level_monitor/worker.py:117-141`",
    "After join(timeout=1.0), check `if thread.is_alive(): log.error(...)` and do NOT clear _level_worker_thread.",
    "🟡 Medium",
)

# === Agent 14: Rust sidecar ===
add(
    44,
    "sidecar/ws.rs WS writer channel 256 cap allows up to 256MB queued memory",
    "❌ Not Fixed",
    "`mpsc::channel::<Message>(256)` with MAX_FRAME_BYTES=1MB means 256 × 1MB = 256MB worst-case pinned memory. Under a"
    "burst of large-payload dispatches, the host can pin up to 256MB in the WS writer channel alone.",
    "On a 4GB machine, ~6% of RAM pinned in the WS channel. The writer task drains slowly if the sidecar's TCP receive "
    "buffer is full.",
    "Channel capacity (256) × max frame size (1MB) = 256MB.",
    "None yet.",
    "- `src-tauri/src/sidecar/ws.rs:666`",
    "Reduce channel capacity to 64 (still enough for bursts, caps at 64MB). Or add a total byte-cap via AtomicUsize.",
    "🟡 Medium",
)

add(
    45,
    "sidecar/spawn.rs stdout-read loop has NO shutting_down check",
    "❌ Not Fixed",
    "The 30s stdout-read loop in spawn_sidecar_release/spawn_sidecar_dev_mode has NO `shutting_down` check. If the user"
    "closes the app during sidecar startup, the host waits up to 30s for server_started before killing the child. The "
    "Tauri RunEvent::Exit path uses a 3s block_on timeout — the spawn task is cancelled at 3s but the child process "
    "keeps running.",
    "Up to 30s of zombie sidecar holding the mic + IPC port during shutdown-during-spawn.",
    "No shutting_down check or tokio::select! on a shutdown notifier in the spawn loop.",
    "None yet.",
    "- `src-tauri/src/sidecar/spawn.rs:281-422`",
    "Add a shutting_down check inside the loop, or use tokio::select! on a shutdown_rx. Kill the child on shutdown "
    "signal.",
    "🟡 Medium",
)

add(
    46,
    "state.rs SidecarHandle::ShellPlugin does NOT kill on Drop → orphan risk",
    "❌ Not Fixed",
    "`SidecarHandle` has no `impl Drop`. If the supervisor task is CANCELLED between cmd.spawn() and the return (e.g. "
    "3s block_on timeout in main.rs), the `child` is dropped WITHOUT kill_tree() being called. CommandChild::Drop does "
    "NOT kill the OS process.",
    "Orphaned Python sidecar process holding the microphone, IPC port, and native hotkey binary child. On next launch, "
    "the new sidecar can't bind the IPC port → spawn fails → supervisor flap loop.",
    "SidecarHandle has no Drop impl; the release-path CommandChild relies on explicit kill_tree() calls.",
    "None yet.",
    "- `src-tauri/src/state.rs:79-82`",
    "Add impl Drop for SidecarHandle that calls kill_tree() on drop (best-effort). Or wrap the spawn path in "
    "tokio::select! that kills the child on cancellation.",
    "🟡 Medium",
)

# === Agent 15: Rust platform ===
add(
    48,
    "platform/logging.rs rotation blocks all loggers — 100ms+ event-loop jank",
    "❌ Not Fixed",
    "`write_line` calls `rotate()` INSIDE the same `guard` Mutex lock scope. `rotate()` performs 3 iterations of "
    "exists()+remove_file()+rename()+chmod() + final rename+chmod = up to ~12 syscalls, all while holding the Mutex. "
    "Every concurrent log::!* call blocks until rotation completes. On a cold disk, each syscall can take 5-50ms, so "
    "rotation can block logging for 100-600ms.",
    "During rotation, the Tauri event loop, WS reader thread, sidecar supervisor, and any Tauri command handler that "
    "calls log::!* all stall. 100ms+ event-loop jank every 5MB of log output under verbose logging.",
    "rotate() is called inside the same guard lock scope as write_line.",
    "None yet.",
    "- `src-tauri/src/platform/logging.rs:1525-1644`",
    "Drop the Mutex BEFORE calling rotate(). The rotation path doesn't need the File handle (it sets *guard = None "
    "first). Use a separate rotation lock or background thread.",
    "🔴 High",
)

add(
    50,
    "platform/open_path.rs xdg-open Child dropped without wait → zombie leak",
    "❌ Not Fixed",
    "`open_path_in_file_manager` does `Command::new('xdg-open').spawn()` and drops the Child immediately. Rust's "
    "Child::Drop does NOT auto-reap — the child remains as a zombie until wait() is called or parent exits. xdg-open "
    "itself typically forks+execs the real file manager and EXITS within ~100ms, but the host never reaps that "
    "short-lived zombie.",
    "Every call to open_logs/open_host_logs/open_model_import_dialog leaks one zombie. A user who opens the logs folder"
    "50 times in a session leaves 50 zombies. Long-running sessions accumulate zombies until process exit.",
    "Child is dropped without wait()/try_wait().",
    "None yet.",
    "- `src-tauri/src/platform/open_path.rs:59-79`",
    "Spawn a tiny reaper task: `tokio::spawn(async move { let _ = child.wait().await; })`. Or install a global SIGCHLD "
    "reaper.",
    "🔴 High",
)

add(
    51,
    "platform/process.rs kill_process_tree race window misses new children",
    "❌ Not Fixed",
    "`kill_process_tree` Phase 1 snapshots descendants via pgrep -P DFS walk, Phase 2 SIGTERMs all collected, sleeps "
    "200ms, Phase 3 SIGKILLs all collected. Between Phase 1 and Phase 2, the sidecar can spawn NEW children that are "
    "NOT in the snapshot. The 200ms sleep makes the window WORSE — a descendant receiving SIGTERM can fork a "
    "cleanup-child during the grace period.",
    "Orphaned grandchildren hold the microphone/IPC port/native hotkey binary child after shutdown — the exact scenario"
    "kill_process_tree was hardened to prevent.",
    "Descendant set is a point-in-time snapshot; no process-group or session-based kill.",
    "None yet.",
    "- `src-tauri/src/platform/process.rs:520-604`",
    "Kill the entire PROCESS GROUP or SESSION instead of individually-collected pids: `kill -TERM -<pgid>` where pgid ="
    "sidecar's process group.",
    "🟡 Medium",
)

# === Agent 16: Rust commands + state ===
add(
    52,
    "state.rs shutdown_sidecar_for_exit holds lock across 2s await — inconsistent with renderer path",
    "❌ Not Fixed",
    "`shutdown_sidecar_for_exit` (RunEvent::Exit path) holds `state.child_exit_rx` AsyncMutex for up to 2s across "
    "`tokio::time::timeout(deadline, rx.recv()).await`. The renderer-invoked `shutdown_sidecar` "
    "(sidecar_cmds.rs:824-827) was FIXED to take the rx out of the lock first. The two shutdown paths are now "
    "inconsistent. On dev-mode, the lock is held for the FULL 2s unconditionally.",
    "On RunEvent::Exit, if the supervisor's respawn path races with the exit, the supervisor's receiver install is "
    "blocked for 2s.",
    "The fix applied to shutdown_sidecar was never propagated to shutdown_sidecar_for_exit.",
    "None yet.",
    "- `src-tauri/src/state.rs:340-376`",
    "Mirror sidecar_cmds.rs:824-827 — take() the rx out of the lock, drop the guard, then await rx.recv() outside the "
    "lock.",
    "🔴 High",
)

add(
    53,
    "state.rs HOST_SHUTDOWN_GRACE_MS dead constant — 5s promise unfulfilled, sidecar force-killed at 2s",
    "❌ Not Fixed",
    "`HOST_SHUTDOWN_GRACE_MS = 5000` is documented as giving the sidecar 5s for graceful shutdown (WAL checkpoint, "
    "native hotkey teardown). But `shutdown_sidecar_for_exit` USES `SHUTDOWN_ACK_TIMEOUT_MS` (2000ms), not "
    "HOST_SHUTDOWN_GRACE_MS. The outer 6s timeout can never fire because the inner function always returns in ≤2s. The "
    "docstring's promise is unfulfilled.",
    "On app exit during a cold-disk WAL checkpoint, the sidecar is force-killed at 2s — exactly the corruption risk the"
    "docstring warns about. history.db can be left in a partial-checkpoint state; native hotkey binary child can be "
    "orphaned.",
    "HOST_SHUTDOWN_GRACE_MS is referenced only at the outer timeout. The inner function hardcodes "
    "SHUTDOWN_ACK_TIMEOUT_MS.",
    "None yet.",
    "- `src-tauri/src/state.rs:340, 404-419, 480-496`",
    "Pass a deadline: Duration parameter into shutdown_sidecar_for_exit. Use HOST_SHUTDOWN_GRACE_MS for the "
    "RunEvent::Exit path.",
    "🔴 High",
)

add(
    54,
    "tray.rs icon re-read + re-decoded from disk on every tray_state event",
    "❌ Not Fixed",
    "`load_tray_icon` re-reads + re-decodes the PNG from disk on every tray_state event that includes an icon field. "
    "There are only 4 icons (idle/recording/transcribing/error). If the Python sidecar emits tray_state at 1Hz for the "
    "tooltip timer, the PNG is re-loaded + re-decoded every second.",
    "During recording, 1 PNG disk read + decode/sec. Compounds over a long recording session. Also spawns a std::thread"
    "per event (~50µs + 2MB virtual stack reservation).",
    "No icon cache; std::thread::spawn instead of spawn_blocking.",
    "None yet.",
    "- `src-tauri/src/tray.rs:104-131, 322-346, 363-407`",
    "Cache the 4 decoded Images in a OnceLock<HashMap>. Replace std::thread::spawn with "
    "tauri::async_runtime::spawn_blocking.",
    "🟡 Medium",
)

add(
    55,
    "sidecar_cmds.rs double serialization of data on dispatch hot path",
    "❌ Not Fixed",
    "dispatch_frame serializes data TWICE: line 517 `serde_json::to_string(data_val).map(|s| s.len())` to check size "
    "(String discarded), then line 610 `frame.to_string()` serializes the whole frame including the cloned data Value. "
    "Plus the data Value is CLONED into the frame at line 541.",
    "For a 256KiB set_config payload, ~512KiB of wasted serialization CPU + ~256KiB of wasted heap allocation + a deep "
    "Value clone per dispatch.",
    "Data is serialized to check length, then re-serialized when the frame is stringified.",
    "None yet.",
    "- `src-tauri/src/commands/sidecar_cmds.rs:515-543, 610`",
    "Serialize data ONCE into a String, check length, build the frame manually with raw JSON embedding.",
    "🟡 Medium",
)

# === Agent 17: Client main python/windows/ipc ===
add(
    56,
    "bubble-handlers.ts webContents.send monkey-patch accumulates on every bubble reload",
    "❌ Not Fixed",
    "The `bubble:ready` handler re-installs the `webContents.send` monkey-patch unconditionally. The renderer fires "
    "bubble:ready once per mount. After a render-process-gone reload (win.reload, NOT destroy+recreate), the SAME "
    "webContents survives → patch re-installs on top. After N reloads, webContents.send is wrapped N layers deep; each "
    "send traverses N closures.",
    "After 10 render-process-gone reloads, every bubble:set-state/bubble:level (30-60Hz during dictation) traverses 10 "
    "closure layers. CPU per send grows O(N); memory grows O(N) closures.",
    "No 'if already patched, skip' guard. webContents persists across win.reload().",
    "None yet.",
    "- `voice_typer/client/src/main/ipc/bubble-handlers.ts:300-313`",
    "Drop the monkey-patch entirely — update _lastKnownBubbleMode at the source in handle-message.ts. Or guard "
    "re-installation with a WeakSet<WebContents>.",
    "🟡 Medium",
)

add(
    57,
    "kill-python.ts is 100 lines of dead code — DRY extraction never wired up",
    "❌ Not Fixed",
    "`kill-python.ts` exports `killPythonProcessWithSigkillFallback` but has ZERO importers. relaunch-app.ts and "
    "stop-python.ts each have their OWN inline copies. The three copies have already diverged (kill-python.ts uses "
    "correct exitCode/signalCode check; others use !proc.killed).",
    "100 lines of dead code that looks load-bearing. Behavioral drift risk: the three copies have inconsistent error "
    "handling.",
    "The DRY extraction was started (kill-python.ts created) but never wired up — the inline copies were never "
    "replaced.",
    "None yet.",
    "- `voice_typer/client/src/main/python/kill-python.ts:1-100`",
    "Either delete kill-python.ts (accept duplication), OR wire up the extraction: relaunch-app.ts and stop-python.ts "
    "call killPythonProcessWithSigkillFallback, then delete inline copies.",
    "🟡 Medium",
)

# === Agent 18: Client main logging ===
add(
    59,
    "structuredLogger.ts mainLogPath/lifecycleLogPath not memoized — app.getPath per log call",
    "❌ Not Fixed",
    "`mainLogPath()`, `lifecycleLogPath()`, `rendererErrorsLogPath()` each call `app.getPath('userData')` + `path.join`"
    "on EVERY invocation. The sibling `printfLogger.ts:getRuntimeLogPath()` IS memoized. Every "
    "logger.warn/error/info(dev) call pays an extra app.getPath + path.join (~1-5µs each).",
    "On a crash-loop path logging 100 errors/sec, ~0.5ms/sec of redundant work on the main event loop. Also feeds into "
    "PERSIST_INFO=1 hot path.",
    "Memoization was added to getRuntimeLogPath (printf) but not ported to the structured logger paths when the logging"
    "module was split.",
    "None yet.",
    "- `voice_typer/client/src/main/logging/structuredLogger.ts:102-104, 227-239`",
    "Add module-level `let _mainLogPath: string | undefined` mirroring printfLogger's pattern. Resolve once on first "
    "call.",
    "🟡 Medium",
)

add(
    60,
    "printfLogger.ts formatArgsForFile called 2x per log.warn/error — 2x regex+stringify",
    "❌ Not Fixed",
    "`log.warn`/`log.error` each call BOTH `writeStdout(...)` AND `mainRuntimeLogger.write(...)` — and BOTH internally "
    "call `formatArgsForFile(args)` which runs redactPii per-arg (up to 10 regex passes) + JSON.stringify "
    "per-non-string-arg. A single log.warn runs the full redact+stringify pipeline TWICE on identical args.",
    "2× the regex/stringify work on every log.warn/log.error call. On hot warn paths (TCP retry storms, bubble window "
    "failures) it doubles formatting CPU.",
    "The two consumers (stdout tee + file tee) were written independently and neither caches the formatted result.",
    "None yet.",
    "- `voice_typer/client/src/main/logging/printfLogger.ts:295-302`",
    "Compute `const formatted = formatArgsForFile(args)` once and pass it to both writeStdout and "
    "mainRuntimeLogger.write.",
    "🟡 Medium",
)

# === Agent 19: Client renderer hooks ===
add(
    61,
    "hotkey-utils.ts formatHotkey re-allocates 40-entry map + calls t() 28 times per call",
    "❌ Not Fixed",
    "`formatHotkey()` declares MAC_MODIFIER_GLYPHS (12 entries) and KEY_LABEL_ALIAS (28 entries, each calling t()) as "
    "function-local constants. Every call re-allocates both maps and invokes t() 28 times. HotkeyPicker calls "
    "formatHotkeyLabel twice per render; with 2-3 instances per Settings page, that's 6 × 40 = 240 map entries + 168 "
    "t() calls per Settings render.",
    "Measurable CPU on Settings page re-renders (theme editing, hex typing, preset hover all trigger re-renders).",
    "Maps written as locals inside function body. t() is locale-dependent so can't be trivially hoisted, but a "
    "locale-keyed cache would eliminate redundant work.",
    "None yet.",
    "- `voice_typer/client/src/renderer/src/components/hotkey/hotkey-utils.ts:342-397`",
    "Hoist MAC_MODIFIER_GLYPHS to module scope (locale-independent). Cache KEY_LABEL_ALIAS keyed by current locale; "
    "invalidate on locale change.",
    "🟡 Medium",
)

add(
    62,
    "bubble/ 11 duplicate IPC subscriptions across 3 hooks + 1 component",
    "❌ Not Fixed",
    "The bubble window registers 11 separate IPC subscriptions: onShow (3 subscriptions), onHide (2), onSetState (2), "
    "onConfig (2), onLevel (1), onDraggable (1). Each Electron IPC listener has per-event callback overhead. The "
    "duplicate onSetState tracker in useAudioLevels vs useBubbleStateMachine can drift.",
    "3× callback overhead per onShow, 2× per onSetState/onConfig/onHide. The duplicate mode tracker can drift between "
    "useAudioLevels (local closure) and useBubbleStateMachine (React state).",
    "Each hook independently subscribes to the same bridge event — no shared subscription layer.",
    "None yet.",
    "- `voice_typer/client/src/renderer/src/bubble/` (multiple files)",
    "Introduce a useBubbleBridge() hook that subscribes once to each event and exposes state via refs/context.",
    "🟡 Medium",
)

# === Agent 20: Client pages + stores ===
add(
    63,
    "themes/index.ts 12 presets statically imported — not lazy-loaded despite docstring claiming so",
    "❌ Not Fixed",
    "themes/index.ts statically imports all 12 preset modules at module-eval time. The docstring at themes.ts:190 "
    "advertises lazy loading via dynamic import() but no code path actually uses it. Every preset ships in the main "
    "bundle.",
    "10-20KB to the main bundle + 12 module evaluations at first paint even though only one is active. Settings "
    "dropdown only needs swatch/name until user selects a preset.",
    "The static import block was never replaced with a dynamic-import registry.",
    "None yet.",
    "- `voice_typer/client/src/renderer/src/themes/index.ts:19-30`",
    "Replace static imports with a registry: THEME_IDS + getThemeById(id) performing dynamic import on demand. Keep "
    "default + custom eager.",
    "🔴 High",
)

add(
    64,
    "useTheme dual instances — App.tsx and Settings.tsx both instantiate, doubling localStorage + IPC",
    "❌ Not Fixed",
    "`useTheme` is a non-singleton hook. App.tsx:238 and Settings.tsx:91 both instantiate it. Every theme-state change "
    "writes 8 localStorage keys (4 per instance). Every backend config_changed event runs two separate setState "
    "cascades.",
    "Doubled localStorage write traffic on every theme/text-size change. Doubled IPC event handler invocations on every"
    "config_changed. Wasted work on every theme toggle.",
    "useTheme is not a singleton — each caller gets its own state slice, localStorage-sync effect, and config_changed "
    "subscription.",
    "None yet.",
    "- `voice_typer/client/src/renderer/src/App.tsx:238`",
    "Lift useTheme state into a Zustand store or React context provider mounted once in App.tsx. Both call sites read "
    "from the single source of truth.",
    "🔴 High",
)

add(
    65,
    "ActivityList not memoized — 600 closure allocations per copy/favorite on 200-row list",
    "❌ Not Fixed",
    "ActivityList creates 3 fresh closures per row inside items.map(). For a 200-row list, 600 closures per render. A "
    "single setCopiedId(item.id) triggers a re-render that re-creates all 600 closures and re-renders all 200 row DOM "
    "nodes — even though only one row changed.",
    "Copy-to-clipboard on any row causes a full list re-render with 600 closure allocations. On a slow machine, "
    "50-150ms of jank.",
    "No React.memo on ActivityList and no row-level memoization.",
    "None yet.",
    "- `voice_typer/client/src/renderer/src/components/dashboard/ActivityList.tsx:139-143`",
    "Extract the row into a memo'd ActivityListRow component. Parent's callbacks are already stable (useCallback), so "
    "memo skips re-render for unchanged rows.",
    "🔴 High",
)

add(
    66,
    "triple transcription_final subscription — 6-7 IPC round-trips per dictation completion",
    "❌ Not Fixed",
    "Home.tsx, History.tsx, and useDashboardData.ts each independently subscribe to 'transcription_final' and "
    "'history_changed' with their own 500ms debounce. A single transcription_final triggers 3 separate get_history + 3 "
    "get_today_stats = 6 IPC round-trips. Dashboard also calls get_history_count = 7 total.",
    "Each transcription completion causes 6-7 backend calls within 500ms. 30-100ms of backend CPU per dictation just to"
    "refresh 3 pages that may not even be visible.",
    "Three independent subscriptions to the same backend events, each with its own debounce timer. No shared "
    "invalidation.",
    "None yet.",
    "- `voice_typer/client/src/renderer/src/pages/Home.tsx:320`",
    "Gate each subscription on visibility — only the active page should refresh. Or introduce a shared 'history "
    "invalidation' store that pages re-fetch lazily when active.",
    "🟡 Medium",
)

with open(REVIEW_PATH, "a", encoding="utf-8") as f:
    for entry in ENTRIES:
        f.write(entry)

print(f"Appended {len(ENTRIES)} IN- entries to {REVIEW_PATH}")
print(f"New review.md size: {os.path.getsize(REVIEW_PATH)} bytes")
