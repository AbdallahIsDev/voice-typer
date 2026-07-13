# ADR 0011: Desktop Runtime Migration to Tauri v2 + PyO3 (Embedded Python)

## Status

Proposed

## Date

2026-07-13

## Context

Voice Typer today is **Electron (React UI) + a separate Python backend**, plus a separate prewarm helper. That is effectively **three processes**:

1. Electron main process (hosts the React UI).
2. `python -m voice_typer.server.ipc_server --port 9876` — the Python backend, spawned by `electron_launcher.py:13`, reached over a local TCP socket.
3. `prewarm.py` — a standalone helper that warms the OS file cache at startup.

Two pain points drive this analysis:

- **(A) IPC middleware dislike.** The UI ↔ Python path is Electron `ipcMain`/`ipcRenderer` → TCP → Python handler mixins (`handlers/*`). The user finds this layered middleman unattractive.
- **(B) "Two things in Task Manager."** Electron + Python ship as two separate programs that cannot be merged into one `.exe` today, so the user sees two processes and wants **one process, one icon**.

This ADR evaluates a **Tauri v2 + PyO3 (embedded Python)** desktop runtime as the replacement for Electron, and records the architecture trade-offs, the wins, the costs, and the documented workarounds. It supersedes the informal "future Rust startup scripts" note in ADR-0009 (§Future Work) by evaluating a full desktop-runtime change, while explicitly preserving the prewarm design from ADR-0009.

> **Plain English:** Today the app is really three programs running at once (the window, the speech brain in Python, and a helper that pre-loads the big model). The user wants the speech brain and the window to be ONE program, and dislikes the "phone line" (IPC) between them. We evaluated a Tauri v2 + PyO3 desktop runtime to see if it lets us do that without throwing away the working Python speech code.

---

## Chosen Runtime — Tauri v2 + embedded Python (PyO3 0.29)

Rust shell hosting the system webview (WebView2 on Windows) **and** an embedded CPython interpreter via PyO3. Your existing Python handler modules run *inside* the Rust binary.

```
One Rust .exe:
  ├─ WebView2 (React UI)                 ← system webview, not bundled Chromium
  ├─ Rust shell (Tauri v2)
  └─ Embedded CPython (PyO3)            ← runs ipc_server / handlers/* in-process
UI → Tauri invoke (Rust) → PyO3 call → Python function
```

| Aspect | Finding |
|--------|--------|
| Process model | **One host process** (Rust + embedded Python). The webview renderer is a child subprocess inherent to any webview GUI (shown under the host), not a separate "app". |
| Single binary | Yes, ~2–10 MB app exe (+ model weights shipped separately, as today). |
| IPC | `invoke` bridge — **lighter and in-process** vs today's TCP server. |
| Python? | **Yes — kept as Python.** No ML rewrite. |
| Maturity | Tauri v2 is production-grade, mobile-capable, plugin ecosystem (tray, autostart, updater). PyO3 0.29 is stable, actively maintained. |

**Verdict:** The **only** candidate that delivers both "one process / one exe" **and** "Python stays Python". This is the recommended direction.

> **Plain English:** Tauri is a Rust program that draws your window using the computer's built-in browser and also hides a real Python interpreter inside itself. Your speech code runs inside that one program. No second `python.exe`, no "phone line" to another program. You keep all your working Python; you only add a thin Rust "front desk" that passes requests from the window to Python.

---

## Decision

**Adopt Tauri v2 + PyO3 (embedded Python) as the target desktop runtime**, replacing Electron. Keep the Python backend and React UI substantially as-is; only the shell + transport change.

Two architectural rules are mandatory and were refined during this analysis:

1. **Keep prewarm as a SEPARATE boot helper.** Do **not** merge it into the app. The main app becomes one process (Electron + Python merged); prewarm remains a distinct, intentional boot-time process that warms the OS file cache. Net: **3 processes → 2 processes** (one app + one tiny invisible boot helper).
2. **Preserve the current streaming model.** Background chunking/streaming stays hidden from the user until dictation ends, then pastes at once. This is a UI choice and is unaffected by the runtime change.

Defer the actual implementation until a **spike** proves PyO3 can embed CPython and load `torch` on the user's Windows machine (see Consequences, Risk 1).

This decision **supersedes** the "rewrite startup scripts in Rust" future-work note in ADR-0009 — we embed Python via PyO3 rather than reimplementing logic in Rust.

> **Plain English (the two rules):** Rule 1 — the pre-load helper stays its own little program so your model stays ready in RAM even after you close the app; we only fuse the *window* and the *speech brain* into one. Rule 2 — the way words are collected in the background and shown all at once does not change.

---

## Consequences

### Great things (optimization, speed, memory) — keep

- **One process / one exe.** Electron main + Python backend become a single Rust host. Task Manager shows one app (+ a normal webview child). Directly resolves complaint (B).
- **No separate `python.exe`, no TCP `:9876`.** The socket IPC server and its relay layer are removed; calls are in-process. Resolves complaint (A) at the transport level.
- **Smaller + lighter.** App exe drops from ~100 MB+ (Electron + bundled Chromium) to ~2–10 MB. Uses system WebView2 (already present on Windows 11) instead of bundling a browser → lower baseline RAM/CPU than Chromium.
- **Faster, cheaper calls.** No JSON-over-TCP serialization of audio chunks / transcripts. PyO3 passes Python objects / NumPy arrays in memory → lower per-call latency on the hot audio path.
- **Model loads once.** Single interpreter init + one model load. The separate prewarm *process* is retained (see below), so cross-restart and boot-time warming are preserved; the prewarm *concept as a second app process competing at logon* is gone.
- **Prewarm benefit preserved.** Prewarm warms the **OS file cache** (RAM the OS manages, not any one process). That cache survives process exit, so reopening the app is still fast. Boot-time warming (before login) is kept via the existing Task Scheduler **BootTrigger**; only the app shell changes, not the scheduler.
- **Lower memory baseline.** One Python runtime instead of two (app + prewarm running simultaneously). GPU model RAM unchanged (~model size).

> **Plain English (why it's faster/smaller):** One program instead of two; no "phone line" copying data back and forth; the app is tiny because it uses the browser Windows already has; the speech model is read from fast RAM (thanks to the kept prewarm helper) instead of the slow disk.

### Bad things (costs) — and the documented workarounds

**Bad 1 — A thin Rust "front desk" is mandatory.**
You don't rewrite the ML in Rust; you write a small Rust layer that embeds Python, exposes Tauri commands, and manages lifecycle. It must be compiled and version-pinned.
*Workaround:* Expose **one generic `invoke_python(method, args)` command** that dispatches to your handler registry, instead of per-command Rust. Maximizes Python reuse, minimizes the Rust you touch. The Rust surface can stay a few hundred lines.

**Bad 2 — libpython linking / "wheels" ABI match.**
The embedded CPython must ABI-match your `torch`/`transformers` wheels, or import crashes.
*Workaround:* Use `python-build-standalone` (portable libpython) + `maturin` to build; **pin the Python version in lockstep** with the torch wheels in CI. This is a routine "keep them matched" chore, not a blocker (confirmed by PyO3 docs: `pyo3` supports CPython 3.8+, PyPy, GraalPy; `auto-initialize` starts the interpreter inside the binary).

**Bad 3 — GIL ("one cashier").**
Python does one thing at a time inside itself; a long transcription job can block the UI if called synchronously.
*Workaround:* Offload long Python work to a **Rust thread / back room**, and push results to the UI via **Tauri events** (a "kitchen bell"). PyO3 documents this parallelism pattern (`pyo3::sync`, detach-from-interpreter for non-Python work). Standard, supported design.

**Bad 4 — Async backend (asyncio).**
If `ipc_server` is async-driven, running its event loop inside an embedded interpreter needs a bridge.
*Workaround:* **`pyo3-async-runtimes`** (listed in PyO3 docs) is a ready-made library bridging Python's asyncio with Rust async runtimes. No hand-rolled glue.

**Bad 5 — `invoke` is still a bridge (lighter).**
There remains a thin UI↔logic connection. The user explicitly accepts this as lighter than today's IPC. No action needed beyond keeping the generic dispatch thin.

**Bad 6 — Cross-language error messages (the "error boundary").**
A Python crash inside the embedded interpreter can surface as a Rust crash with a Python note attached — harder to read than today's separate Python process.
*Workaround (confirmed by PyO3 docs):* PyO3 represents any Python failure as **`PyErr`**, which **captures the full Python traceback**. Wrap every Python call in one safety layer (the "error boundary" the user intuited) that: (1) catches `PyErr`, (2) writes the **complete Python traceback** to the normal log file, (3) shows the user a calm, friendly message. PyO3 also supports `create_exception!` / `import_exception!` to type and route errors cleanly. This converts the "scrambled mixed-language crash" into a clean, logged, debuggable event. Optionally set a Python `sys.excepthook`/`sys.unraisablehook` to catch any unhandled Python error globally and route it to the log.

**Bad 7 — Webview consistency.**
The UI uses the OS-built-in browser (WebView2 on Win11). Slightly different rendering across OSes vs Electron's identical bundled Chromium.
*Workaround:* Negligible for a Windows-first app; minor CSS/API guardrails if macOS/Linux support is later added.

> **Plain English (the workarounds):** Every "bad thing" has a known fix. The Rust layer can be tiny (one pass-through command). The Python version is pinned once in the build. Long jobs run in a back room and ring a bell with results. Async is handled by an existing library. And the scary mixed error becomes a clean log entry + friendly popup, exactly the "error boundary" idea — PyO3 is built to support it.

### Streaming clarification (corrected during analysis)

The app **does** chunk/stream in the background while talking, but the text is **hidden** until the user finishes, then pasted at once (to avoid distracting the user). This is a UI choice and carries over **unchanged** to Tauri. No architectural change required.

### Prewarm clarification (corrected during analysis)

The user keeps prewarm separate on purpose: closing the app leaves the model in RAM (via the OS file cache) so the next open is instant, and prewarm can start at **boot, before login**. Merging prewarm into the app would discard that benefit. Therefore prewarm **stays a separate boot helper** after migration. The migration fuses only Electron + Python into one process; prewarm remains distinct. Boot-time (pre-login) warming is preserved via the existing Task Scheduler BootTrigger. Result: **3 processes → 2** (one app + one invisible boot helper), with the original speed trick intact.

> **Plain English:** The pre-load helper is a feature, not a flaw — it keeps your model ready even after you close the app, and it can start before you log in. We keep it. We only merge the window and the speech brain. So you go from three running programs down to two, and the "instant load" trick still works.

---

## Risks / Open Questions

1. **Embedding spike required.** The hardest, make-or-break step is embedding CPython + loading `torch` on Windows with correct ABI/DLL paths. **Action:** run a minimal Tauri v2 + PyO3 app that embeds Python and imports `torch` on the user's machine before any full migration is planned.
2. **Rust toolchain added to build.** Increases CI complexity vs pure Python/Node. Mitigated by keeping Rust minimal (Bad 1 workaround) and using `maturin`.
3. **Cross-language debugging friction** (Bad 6) is an ongoing, not one-time, cost — mitigated by the `PyErr` error boundary, but the builder must maintain it.
4. **Webview feature parity** for any UI bits relying on Chromium-only APIs — verify during spike.

## References

- ADR-0001 (Electron + Python Architecture, Accepted) — current architecture being replaced.
- ADR-0009 (Prewarm & Autostart Architecture) — prewarm/boot-trigger design preserved by this ADR.
- PyO3 user guide v0.29.0 (pyo3.rs/v0.29.0) — embedding (`auto-initialize`), `PyErr`/traceback capture, `pyo3-async-runtimes`, GIL/parallelism.
- `electron_launcher.py:13` — current spawn of `python -m voice_typer.server.ipc_server --port 9876`.
- `voice_typer/server/prewarm.py`, `task_scheduler.py` — prewarm + BootTrigger (kept).

*End of document.*
