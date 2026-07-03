# ADR 0008 — Dependency Injection Boundary for IPCServer

**Status**: Accepted
**Date**: 2026-07-03
**Related**: ARCH-REFAC-004 section in `docs/ARCHITECTURE.md`, ADR 0007 (audio filter chain architecture — unrelated domain but shares the "thin seam + protocol" approach)

---

## 1. Context

`IPCServer.__init__(app)` historically took a concrete
`VoiceTyperApp` and immediately constructed a `VoiceTyperService(app)`
inside its own constructor. This produced tight coupling between the
IPC dispatch layer and the service implementation:

- **IPC tests could not isolate dispatch from service.** Every test
  that constructed `IPCServer(app)` — and there are **20+ such test
  files** across `tests/` — ended up with a real `VoiceTyperService`
  wrapping whatever `app` was passed (often a `MagicMock`). Bugs in
  the service layer surfaced as IPC test failures, and IPC dispatch
  behavior could not be exercised in isolation.
- **Mock-based apps leaked service construction.** Passing a
  `MagicMock` as `app` does not stop `IPCServer` from calling
  `VoiceTyperService(app)`; the service constructor then runs real
  initialization code against a mock, which can either no-op silently
  or raise unrelated errors far from the code under test.
- **No single composition root.** Wiring decisions (which service
  class to use, how to configure it, whether to wrap it for logging
  or metrics) were embedded in `IPCServer.__init__` itself, so future
  changes would require touching every call site rather than one
  factory.

### Previous-round decision to defer DI

Round 1 of the refactor effort explicitly skipped introducing a DI
seam here. The reasoning, recorded in the verification findings, was:

- **Severity was low.** The tight coupling is a testability smell,
  not a correctness bug — production behavior is unaffected.
- **The fix looked invasive.** A naive constructor-injection-only
  refactor (`IPCServer(app, service)`) would have forced every one of
  the 20+ test files that call `IPCServer(app)` to change in lockstep,
  ballooning the diff and the review surface for a low-severity
  improvement.
- **Other priorities ranked higher.** Test renaming, CI tooling
  migration, microphone polling, and the audio filter chain (ADR
  0007) all scored higher on the impact/effort axis.

By Round 2 the calculus changed: the test suite had grown further,
IPC handler coverage was actively being expanded, and the need to
substitute a fake service for dispatch-layer tests became a blocker
rather than a nicety. This ADR records the resolution.

---

## 2. Decision

Adopt a **Protocol-based DI seam** with a **backward-compatible
constructor** and a **canonical factory** for production.

### 2.1 Protocols in `providers.py`

Two `typing.Protocol` classes are defined in
`voice_typer/server/providers.py`:

- **`AppProtocol`** — the structural type for the `app` object
  consumed by `IPCServer` and its handler mixins. Members are the
  public domain objects (`config`, `history_db`, `models`,
  `recording`, `hotkeys`, `recorder`, `tray`), the private attributes
  the handlers / IPC server reach into (`_audio_processor`,
  `_volume_ducker`, `_ipc_server`, `_config_mutation_lock`,
  `_shutting_down`), and the methods the service layer delegates to
  the app (`change_model`, `toggle_dictation`, `undo_last`,
  `repaste_last`, `restart_app`, `quit_app`, `start`).
- **`ServiceProtocol`** — the structural type for the `service`
  object consumed by the IPC handler mixins. This enumerates the full
  `VoiceTyperService` public method surface (status, dictation,
  config, history, microphone, models, vocabulary, templates,
  onboarding, system).

Both protocols are `@runtime_checkable` and use `typing.Any` for
member types. This is deliberate:

1. The protocol module does not import every concrete dependency
   (avoiding import cycles and a heavy import surface).
2. Test doubles (`MagicMock`, custom fakes) trivially satisfy the
   protocols via structural typing — no inheritance required.
3. The protocols capture **shape**, not type identity, which is the
   whole point of structural subtyping.

### 2.2 Backward-compatible constructor

`IPCServer.__init__` accepts an optional `service` parameter:

```python
def __init__(self, app, service: Optional[Any] = None) -> None:
    self.app = app
    if service is not None:
        self.service = service           # DI mode: caller-provided fake
    else:
        from voice_typer.server.service import VoiceTyperService
        self.service = VoiceTyperService(app)  # Backward compat
```

- **`IPCServer(app)`** — backward-compatible path. Used by all 20+
  existing test files and the production entry point. Constructs a
  real `VoiceTyperService(app)` exactly as before. No call site needs
  to change.
- **`IPCServer(app, service=fake_service)`** — DI path. Used by tests
  that want to exercise the IPC dispatch layer in isolation. The
  injected `service` is stored verbatim on `self.service`; no
  `VoiceTyperService` is constructed.

`app` is annotated as `Any` (not `AppProtocol`) on the constructor
signature so existing `MagicMock`-based test fixtures keep working
without importing the protocol module. `AppProtocol` is structural —
a `MagicMock` already satisfies it — but annotating the parameter
with `AppProtocol` would force every test file that constructs
`IPCServer(app)` to add an import, which is unnecessary migration
burden for no behavioral gain.

### 2.3 Composition root: `providers.build_ipc_server(app)`

`voice_typer/server/providers.py` exports a factory:

```python
def build_ipc_server(app: AppProtocol) -> IPCServer:
    from voice_typer.server.ipc_server import IPCServer
    return IPCServer(app)
```

This is the **canonical composition root** for production code. The
production entry point (`voice_typer/server/ipc_server.py:main`) calls
`build_ipc_server(app)` instead of `IPCServer(app)` directly.

Behavior today is identical to `IPCServer(app)`: a real
`VoiceTyperService` is constructed over `app`. The factory exists so
that future wiring changes (logging, metrics, feature flags, an
alternate service implementation) live in one place rather than being
threaded through every call site. The factory intentionally does
**not** accept a `service` parameter — tests that want DI should call
`IPCServer(app, service=fake)` directly, keeping the production path
and the test path visually distinct.

### 2.4 Test helpers

`tests/fixtures/ipc_test_helpers.py` provides ready-made fakes:

- `make_fake_app()` — a `MagicMock` configured with every attribute
  `AppProtocol` requires.
- `make_fake_service()` — a `MagicMock` satisfying `ServiceProtocol`
  with sensible default return values for the most-called methods.
- `make_ipc_server_with_fakes()` — returns
  `(server, fake_app, fake_service)` for tests exercising `IPCServer`
  in isolation.

### 2.5 Protocol drift detection

`tests/test_di_providers.py` walks the AST of every handler under
`voice_typer/server/handlers/` (and `ipc_server.py` itself) and
collects every `self.app.<name>` and `self.service.<name>` access,
then asserts each one is declared on the corresponding protocol
(`test_app_protocol_lists_all_attributes_used_by_handlers`). If a
future handler reads `self.app.new_field` without `new_field` being
declared on `AppProtocol`, the introspection test fails — forcing an
explicit decision about whether to widen the protocol (accepted
surface growth) or refactor the handler to go through the service
layer (preferred — the protocol surface should stay small).

---

## 3. Consequences

### 3.1 Positive

- **IPC tests can isolate the dispatch layer.** Tests using
  `make_ipc_server_with_fakes()` exercise `_dispatch`, `_send`, and
  the `_handle_*` mixins without depending on `VoiceTyperService`
  behavior. Service-layer bugs no longer cascade into IPC test
  failures.
- **Protocol drift is detectable.** The introspection test in
  `tests/test_di_providers.py` catches any new `self.app.X` or
  `self.service.X` access that the protocol doesn't list, so the
  structural contract can't silently widen.
- **Single composition root.** Future wiring (logging decorators,
  metrics, feature-flag-gated service selection, tracing) has an
  obvious home in `build_ipc_server` without touching `IPCServer`
  itself or its call sites.
- **No migration burden.** All 20+ existing test files and the
  production entry point continue to call `IPCServer(app)` unchanged.

### 3.2 Negative

- **Two construction patterns coexist.** `IPCServer(app)` (legacy /
  production) and `IPCServer(app, service=fake)` (DI / test) both
  work. This is **intentional backward compatibility** — documented
  here and in the migration table in `ARCHITECTURE.md` — but it means
  a reader of `IPCServer.__init__` must understand both paths. The
  alternative (forcing all callers to pass `service` explicitly) was
  rejected as too invasive; see §4.
- **`app` parameter is typed `Any` rather than `AppProtocol`.** This
  trades a small amount of static-type-checker coverage for keeping
  `MagicMock`-based test fixtures import-free of the protocol module.
  The runtime_checkable protocol can still be used with
  `isinstance(app, AppProtocol)` in any code that wants the check.
- **Protocols use `Any` for member types.** A future contributor
  reading `AppProtocol.config: Any` gets less information than
  `config: Config` would provide. The trade-off is that the protocol
  module avoids importing every concrete dependency — keeping it
  lightweight and cycle-free — and the docstrings on each member
  point to the concrete class.

### 3.3 Neutral

- **`providers.py` is a thin module today.** It defines two protocols
  and one ~3-line factory. Future wiring (logging, metrics, feature
  flags, alternate service implementations) can be added there
  without expanding `IPCServer`'s constructor surface or touching
  call sites.
- **`ServiceProtocol` enumerates the full service surface.** This is
  a large protocol (~50 methods), but it mirrors the existing
  `VoiceTyperService` public surface 1:1 — no new surface is being
  invented, just documented.

---

## 4. Alternatives Considered

1. **Full DI container (e.g. `dependency-injector` library).**
   Rejected. Adds a third-party dependency, requires declarative
   container configuration that doesn't match the codebase's
   manual-construction style, and provides capabilities (lifecycle
   management, scoped resolution) far beyond what this codebase
   needs. The protocol + factory pattern achieves the testability
   goal with zero dependencies.

2. **Constructor injection only (remove backward-compat
   `IPCServer(app)`).** Rejected. Would force every one of the 20+
   test files that currently call `IPCServer(app)` to be edited in
   lockstep — a large, mechanical, review-heavy diff for a
   low-severity improvement. The backward-compatible `service=None`
   default keeps the seam purely additive.

3. **Service locator pattern** (e.g. `IPCServer` calls
   `ServiceLocator.get_service()` internally). Rejected. Service
   locators are widely considered an anti-pattern: they hide
   dependencies (the constructor signature no longer tells you what
   the class needs), make testing harder rather than easier (you
   have to know which locator key to override), and introduce global
   state. Constructor injection via the optional `service` parameter
   keeps dependencies explicit in the signature.

4. **Monkeypatch `VoiceTyperService` in tests.** Rejected. Already
   used in a few legacy tests, but it's brittle: it mutates global
   state, can leak across test files if cleanup is missed, and
   doesn't help with the protocol-drift-detection goal (there's no
   declarative surface to introspect).

5. **Do nothing (defer again).** Rejected. The test suite was
   actively growing new IPC handler tests, and the inability to
   isolate dispatch from service was already producing flaky,
   hard-to-debug failures. The cost of the seam (one thin module +
   one optional constructor parameter) was significantly lower than
   the ongoing cost of working around the coupling.

---

## 5. References

- `voice_typer/server/providers.py` — defines `AppProtocol`,
  `ServiceProtocol`, and `build_ipc_server`.
- `voice_typer/server/ipc_server.py` — `IPCServer.__init__` with the
  optional `service` parameter; `main()` calls `build_ipc_server`.
- `tests/test_di_providers.py` — protocol-drift regression test
  (`test_app_protocol_lists_all_attributes_used_by_handlers`).
- `tests/fixtures/ipc_test_helpers.py` — `make_fake_app`,
  `make_fake_service`, `make_ipc_server_with_fakes`.
- ARCH-REFAC-004 section in `docs/ARCHITECTURE.md` — operational
  documentation including the migration table.

---

End of ADR.
