"""Extracted models submenu logic from tray.py.

_build_models_submenu data enumeration was previously inline
in TrayIcon.  The data-gathering logic is now a standalone function
so it can be tested independently and potentially shared.

previously every menu rebuild (every right-click on the
tray icon) called ``ensure_hf_env()`` and ran 5+ filesystem
``exists()`` checks.  This caused noticeable menu-open lag.  We now
cache the HuggingFace hub ``refs/main`` existence check (with a
5-second TTL so a download started in the Models page is reflected
within 5 seconds without making the user wait on every right-click).
(An older ``import qwen_asr`` availability cache was removed 2026-08-15
with the torch Qwen engine — Qwen is a built-in ONNX backend now.)

Qwen availability (its ``downloaded`` flag in the submenu data) is
NOT the qwen_asr import alone: it mirrors the Models page's
``get_model_status``, requiring model WEIGHTS on disk (the configured
``qwen_model_path`` directory OR the HF cache holding
``models--Qwen--Qwen-Audio``) in addition to the ``qwen_asr`` package
being importable. Previously the tray gated Qwen only on the package
import, so Qwen appeared selectable with zero weights downloaded.
"""

import json
import logging
import time
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

# caches for the model availability checks.
# These are module-level because the tray menu is rebuilt on every
# right-click and the underlying data (HF model downloaded) changes
# very rarely — only when the user finishes a model download.
# Cache of (repo_id, config_dir) → (downloaded, timestamp).
# TTL is 5 seconds: long enough to avoid per-right-click stat() calls,
# short enough that a download finishing in the Models page is
# reflected on the next right-click within 5 seconds.
_HF_DOWNLOAD_CACHE_TTL_SECONDS = 5.0
_hf_download_cache: "dict[tuple[str, str], tuple[bool, float]]" = {}
# ``ensure_hf_env()`` mutates process-global environment
# variables (``HF_HOME``, ``HF_HUB_CACHE``, ``TRANSFORMERS_CACHE``).
# It is documented as idempotent and only needs to run ONCE per
# process — the env state never changes mid-session. Calling it on
# every tray right-click (which triggers a fresh ``import``
# resolution + dict update + permission check on the cache dir) was
# pure overhead. The flag is set on first invocation and short-
# circuits all subsequent calls. ``invalidate_model_availability_cache``
# does NOT reset this — env setup is process-lifetime, not
# per-right-click.
_hf_env_ensured: bool = False

# Qwen's HuggingFace repo id — informational only: the backend
# registry (``model_registry.py``) declares Qwen ``network_behavior=
# "local-only"``, so the weights are NEVER auto-fetched. They live
# either under the configured ``qwen_model_path`` or in the HF cache
# under this repo. Mirrors ``ModelMetadata.repo_id`` for "qwen" so the
# tray and the Models page's ``get_model_status`` agree on what
# "downloaded" means.
# The pre-exported ONNX repo (torch-free, 2026-08-15) — the old torch
# ``Qwen/Qwen-Audio`` repo_id was removed with the torch engine.
_QWEN_REPO_ID = "andrewleech/qwen3-asr-1.7b-onnx"

# Parakeet's ONNX export repo (fp16, torch-free) — informational
# only: the backend registry (``model_registry.py``) declares Parakeet
# ``network_behavior="local-only"``, so the weights are NEVER
# auto-fetched. They live either under the configured
# ``parakeet_model_path`` or in the HF cache under this repo. Mirrors
# ``ModelMetadata.repo_id`` for "parakeet" so the tray and the Models
# page's ``get_model_status`` agree on what "downloaded" means.
# ``grikdotnet/parakeet-tdt-0.6b-fp16`` is the upstream original fp16
# export (switched 2026-08-20; the earlier visuall copy was a re-upload
# of the same files minus config.json).
_PARAKEET_REPO_ID = "grikdotnet/parakeet-tdt-0.6b-fp16"

# Native tray menus are text-only: pystray's ``MenuItem`` has no image
# support (verified against the installed package 2026-08-15), so the
# family logos shown on the Models page (``src/assets/models/``) cannot
# be rendered as real images here. The closest faithful equivalent is a
# per-family Unicode mark prefixed to each model label — the same glyph
# for every model in a family. The glyphs approximate each brand mark:
# ``✱`` ≈ OpenAI's knot (Whisper family), ``◉`` ≈ NVIDIA's eye
# (Parakeet), ``⊙`` ≈ Qwen's ring. All three render in the system
# fonts (Segoe UI / SF Pro / default GTK) on Windows, macOS, and Linux.
_FAMILY_MENU_GLYPHS: dict[str, str] = {
    "whisper": "✱",
    "parakeet": "◉",
    "qwen": "⊙",
}

# model name → backend, mirroring the ``candidates`` list in
# ``build_models_submenu_data`` (and ultimately ``MODEL_REGISTRY``).
# Used to attach the family glyph to each tray label. Keep in sync when
# the catalog changes so a new model degrades gracefully to a bare name
# (no glyph) rather than crashing the menu build.
_MODEL_BACKENDS: dict[str, str] = {
    "tiny": "whisper",
    "large-v3": "whisper",
    "large-v3-turbo": "whisper",
    "parakeet": "parakeet",
    "qwen": "qwen",
}


def _menu_label(name: str) -> str:
    """Return the tray submenu label for a model: family glyph + name.

    Unknown model names (not in ``_MODEL_BACKENDS``) fall back to the
    bare name so a future catalog addition degrades gracefully instead
    of raising or rendering an empty prefix.
    """
    glyph = _FAMILY_MENU_GLYPHS.get(_MODEL_BACKENDS.get(name, ""), "")
    return f"{glyph} {name}" if glyph else name


def _ensure_hf_env_once() -> None:
    """run ``asr_setup.ensure_hf_env()`` exactly once per process.

    The function mutates ``os.environ`` (setting HF_HOME / HF_HUB_CACHE /
    TRANSFORMERS_CACHE paths) — those values are process-global and never
    change after the first call, so re-running on every tray right-click
    was wasted work (a ``dict.update`` on ``os.environ`` + a pathlib
    resolve + ``mkdir(parents=True, exist_ok=True)`` per call). Cached
    behind a module-level bool so the second and subsequent invocations
    are a single boolean check.
    """
    global _hf_env_ensured
    if _hf_env_ensured:
        return
    from voice_typer.server.asr_setup import ensure_hf_env

    ensure_hf_env()
    _hf_env_ensured = True


def _check_hf_model_downloaded(repo_id: str, config_dir) -> bool:
    """Return True if the HuggingFace model ``repo_id`` is downloaded.

    cached for 5 seconds so a tray right-click doesn't
        trigger 5 filesystem ``exists()`` calls (one per candidate model).
        A download finishing in the Models page is reflected on the next
        right-click within the TTL window.
    """
    cache_key = (repo_id, str(config_dir))
    now = time.monotonic()
    cached = _hf_download_cache.get(cache_key)
    if cached is not None:
        downloaded, ts = cached
        if now - ts < _HF_DOWNLOAD_CACHE_TTL_SECONDS:
            return downloaded
    cache_dir = config_dir / "huggingface" / "hub"
    ref_file = cache_dir / f"models--{repo_id.replace('/', '--')}" / "refs" / "main"
    downloaded = ref_file.exists()
    _hf_download_cache[cache_key] = (downloaded, now)
    return downloaded


def _check_qwen_model_downloaded(config_dir, qwen_model_path) -> bool:
    """Return True if the Qwen model WEIGHTS are on disk.

    Mirrors ``ModelMixin._compute_model_status`` (service/model.py):
    ``downloaded`` means the configured ``qwen_model_path`` points at
    an existing directory OR the HuggingFace cache holds
    ``models--Qwen--Qwen-Audio``.

    This is deliberately distinct from the ``qwen_asr`` pip-package
    import check — a package can be installed with ZERO weights on
    disk. The tray previously used the package import as Qwen's
    availability gate, so Qwen appeared selectable (and failed on
    click at engine-load time) whenever ``qwen_asr`` was installed but
    no model weights were downloaded. The call site combines this
    weights check with the package-import check (the ``deps_ok``
    equivalent) to mirror the Models page, which only offers Select
    when ``downloaded && deps_ok`` both hold.
    """
    if isinstance(qwen_model_path, str) and Path(qwen_model_path).is_dir():
        return True
    return _check_hf_model_downloaded(_QWEN_REPO_ID, config_dir)


def _check_parakeet_model_downloaded(config_dir, parakeet_model_path) -> bool:
    """Return True if the Parakeet model WEIGHTS are on disk.

    Mirrors ``ModelMixin._compute_model_status`` (service/model.py):
    ``downloaded`` means the configured ``parakeet_model_path`` points
    at an existing directory OR the HuggingFace cache holds
    ``models--grikdotnet--parakeet-tdt-0.6b-fp16``.
    """
    if isinstance(parakeet_model_path, str) and Path(parakeet_model_path).is_dir():
        return True
    return _check_hf_model_downloaded(_PARAKEET_REPO_ID, config_dir)


def is_active_model_downloaded(config) -> bool:
    """Return True if the currently-configured ASR model is on disk.

    Fast single-model probe mirroring ``ModelMixin._compute_model_status``
    (service/model.py) for the ACTIVE backend only — one TTL-cached
    HF-cache check (or path ``isdir``) instead of the full registry
    scan. Used by:

    - ``TrayIcon._compute_tooltip`` — only show the ``[model]`` suffix
      when the model is actually downloaded (a stale ``model_size``
      selected before the model was deleted must not be advertised).
    - ``ModelManager._model_downloaded_precheck`` — refuse the load
      attempt early (before the heavy engine import + LOADING state)
      when the configured model is definitively absent.

    Cloud backends (openai / groq / deepgram / custom) have no local
    model to gate — returns True (nothing to refuse). Unknown model
    sizes (not in ``MODEL_REGISTRY``) also return True and let the
    load path surface its own error. Non-``Config`` objects (test
    doubles) return True so probes never run against a real user cache
    from a unit test.
    """
    # Guard: only probe against a REAL Config. Test doubles
    # (MagicMock / SimpleNamespace / local ``_Config`` classes) are
    # duck-typed for ``load_background`` but must not read the real
    # user's HF cache — the probe would be nondeterministic and the
    # pre-check would misfire. In production ``app.config`` is always a
    # real ``Config``.
    from voice_typer.server.config import Config as _ConfigCls

    if not isinstance(config, _ConfigCls):
        return True
    from voice_typer.server.config import _config_dir

    config_dir = _config_dir()
    backend = getattr(config, "asr_backend", "whisper") or "whisper"
    from voice_typer.server.model_registry import NO_MODEL_SIZE

    # Qwen / Parakeet are selected via ``asr_backend``, NOT via
    # ``model_size`` — their weights check is independent of the empty
    # "no model selected" sentinel, so a config with ``asr_backend =
    # "parakeet"`` + ``model_size = ""`` (e.g. a user who set the model
    # path in Settings) still reports the installed weights correctly.
    if backend == "qwen":
        return _check_qwen_model_downloaded(config_dir, getattr(config, "qwen_model_path", None))
    if backend == "parakeet":
        return _check_parakeet_model_downloaded(config_dir, getattr(config, "parakeet_model_path", None))
    # Whisper: "No model selected" (``model_size == ""``) — there is
    # nothing to probe. Return False (definitively absent) so the load
    # path refuses with a "No model selected" message instead of trying
    # to load a model for the empty size, and the tray tooltip shows no
    # model suffix.
    if getattr(config, "model_size", None) == NO_MODEL_SIZE:
        return False
    if backend in ("whisper", "distil-whisper"):
        model_size = getattr(config, "model_size", "") or ""
        from voice_typer.server.model_registry import get_model_metadata

        meta = get_model_metadata(model_size)
        if meta is None:
            # Unknown model size — let the load path surface its own
            # error rather than refusing on a guessed repo id.
            return True
        return _check_hf_model_downloaded(meta.repo_id, config_dir)
    # cloud / custom / unknown backend — no local model gate.
    return True


def invalidate_model_availability_cache() -> None:
    """Invalidate the cached model availability checks.

    Called by the model download path (Models page) so the next tray
    right-click reflects the newly-downloaded model immediately,
    without waiting for the TTL to expire.
    """
    _hf_download_cache.clear()


def build_models_submenu_data(
    config_dir_fn,
    controller_change_model_fn,
    *,
    config_provider=None,
) -> list[tuple[str, bool, bool, Any]]:
    """Gather model info for the tray models submenu.

        Returns a list of tuples: (name, is_downloaded, is_active, change_fn)

        Parameters:
            config_dir_fn: callable returning the config directory Path
            controller_change_model_fn: callable(name) to change the active model
    config_provider: optional live Config object. : when provided,
                uses ``config_provider.asr_backend`` / ``config_provider.model_size``
                instead of re-parsing config.json from disk. Falls back to disk
                read when None.
    """
    # ensure_hf_env() is process-global idempotent; cache it
    # behind ``_ensure_hf_env_once`` so right-click menu rebuilds don't
    # repeat the env-var setup work on every invocation.
    _ensure_hf_env_once()

    # prefer the in-memory Config object over a disk read.
    # Falls back to disk read when config_provider is None (e.g. tests).
    current_model = "tiny"
    cfg: dict = {}
    qwen_model_path: Any = None
    if config_provider is not None:
        # ``model_size == ""`` (NO_MODEL_SIZE) must stay ``""`` — the
        # ``or "tiny"`` fallback only applies when the attribute is
        # MISSING (None), so a genuine "no model selected" state does
        # not get relabeled as tiny (which would mark tiny active in
        # the tray submenu).
        current_model = (
            ""
            if getattr(config_provider, "model_size", None) == ""
            else (getattr(config_provider, "model_size", "tiny") or "tiny")
        )
        current_backend = getattr(config_provider, "asr_backend", "whisper") or "whisper"
        qwen_model_path = getattr(config_provider, "qwen_model_path", None)
    else:
        # Read current model from config.json on disk.
        config_path = config_dir_fn() / "config.json"
        try:
            with open(config_path) as f:
                cfg = json.load(f)
            current_model = cfg.get("model_size", "tiny")
        except Exception as exc:
            # Previously a bare ``except Exception: pass`` —
            # if ``config.json`` is corrupt, missing, or unreadable,
            # ``cfg`` stayed ``{}`` and the tray menu silently showed
            # ``model_size="tiny.en"`` + ``asr_backend="whisper"``
            # regardless of the user's actual configuration. Log at
            # DEBUG (not WARNING) because the tray menu falling back
            # to defaults is non-fatal — the user can still open
            # Settings to reconfigure. The ``exc_info=True`` ensures
            # the traceback lands in ``voice-typer.log`` for diagnosis.
            log.debug(
                "[TRAY] failed to read config.json for tray menu: %s",
                exc,
                exc_info=True,
            )
            cfg = {}
        current_backend = cfg.get("asr_backend", "whisper") if cfg else "whisper"
        qwen_model_path = cfg.get("qwen_model_path") if cfg else None

    # Models to check — mirrors MODEL_REGISTRY (Whisper family:
    # tiny / large-v3 / large-v3-turbo; ``large-v3`` restored
    # 2026-08-15 at the user's request).
    candidates = [
        ("tiny", "whisper", "Systran/faster-whisper-tiny"),
        ("large-v3", "whisper", "Systran/faster-whisper-large-v3"),
        ("large-v3-turbo", "whisper", "Systran/faster-whisper-large-v3-turbo"),
        ("parakeet", "parakeet", "nvidia/parakeet-tdt-0.6b-v3"),
        ("qwen", "qwen", None),
    ]

    results = []
    config_dir = config_dir_fn()

    for name, backend, repo_id in candidates:
        downloaded = False
        if backend == "qwen":
            # Qwen is a built-in ONNX backend now (qwen_onnx_model.py —
            # no pip package gate; onnxruntime is a base dependency).
            # ``downloaded`` means an ONNX model dir is on disk
            # (``qwen_model_path`` dir OR HF cache), matching the Models
            # page's ``get_model_status`` semantics.
            downloaded = _check_qwen_model_downloaded(config_dir, qwen_model_path)
        elif repo_id:
            # cached check with 5-second TTL — avoids
            # 5× filesystem exists() per right-click.
            downloaded = _check_hf_model_downloaded(repo_id, config_dir)
        else:
            downloaded = False

        # ``no_model``: with ``model_size == ""`` the user has NO active
        # model — nothing in the submenu may render as active (the
        # parakeet/qwen branches below are backend-keyed and would
        # otherwise light up their row).
        no_model = current_model == ""
        is_active = not no_model and (
            (name == current_model and current_backend == backend)
            or (name == "parakeet" and current_backend == "parakeet")
            or (name == "qwen" and current_backend == "qwen")
        )

        results.append((name, downloaded, is_active, lambda n=name: controller_change_model_fn(n)))

    return results


def build_models_menu_items(
    config_dir_fn,
    controller_change_model_fn,
    wrap_fn,
    open_electron_window_fn,
    menu_item_class=None,
    menu_separator=None,
    config_provider=None,
    # localization callable. ``localize("more_models")`` returns
    # the user-facing label for the trailing "More models..." item.
    localize=None,
):
    """#13: Build the full list of pystray MenuItems for the Models submenu.

        Fully extracts the pystray UI glue from TrayIcon._build_models_submenu.
        Accepts pystray.MenuItem and pystray.Menu.SEPARATOR as parameters so
        the module doesn't import pystray at module level (testable without it).

        Parameters:
            config_dir_fn: callable returning the config directory Path
            controller_change_model_fn: callable(name) to change the active model
            wrap_fn: callable wrapping a function for pystray's callback pattern
            open_electron_window_fn: callable to open the Electron app
            menu_item_class: pystray.MenuItem class (default: pystray.MenuItem)
            menu_separator: pystray.Menu.SEPARATOR (default: pystray.Menu.SEPARATOR)
    config_provider: optional live Config object. : when provided,
                the data builder uses ``config_provider.asr_backend`` /
                ``config_provider.model_size`` instead of re-parsing config.json
                from disk. Falls back to disk read when None.
            localize: optional ``Callable[[str], str]`` for label localization.
                When provided, the trailing "More models..." item label is
                ``localize("more_models")`` (with the literal English fallback
                preserved for source-level regression tests). When None, the
                English literal is used directly.
    """
    if menu_item_class is None:
        import pystray

        menu_item_class = pystray.MenuItem
    if menu_separator is None:
        import pystray

        menu_separator = pystray.Menu.SEPARATOR

    # prefer the localized label when a localize callable is
    # provided; fall back to the English literal so the source still
    # contains the "More models..." string (tests/tauri/mig19/
    # test_tray_menu.py asserts the literal substring is present).
    more_models_label = "More models..."
    more_models_text = more_models_label
    if localize is not None:
        try:
            more_models_text = localize("more_models") or more_models_label
        except Exception:
            more_models_text = more_models_label

    items = []
    for name, downloaded, is_active, change_fn in build_models_submenu_data(
        config_dir_fn,
        controller_change_model_fn,
        config_provider=config_provider,
    ):
        if not downloaded:
            continue
        # Native checkmark: pystray's MenuItem ``checked`` parameter
        # renders the platform-standard checkmark on the active model
        # (Win32: MF_CHECKED; macOS: NSControlStateValueOn; GTK:
        # RadioMenuItem active). Previously we manually prefixed the
        # label with "• " (and non-active with "  "), which bypassed
        # the native checkmark and broke screen-reader semantics.
        # ``checked`` MUST be a callable — pystray wraps it via
        # ``_assert_callable(checked, lambda _: None)`` and invokes it
        # as ``checked(item)`` at render time; a raw bool raises
        # ``ValueError`` at MenuItem construction (crashes the tray
        # at startup). The menu is rebuilt on every right-click via
        # invalidate_menu_cache, so the captured bool is fresh at
        # display time.
        # Label = family glyph + name (see ``_menu_label``) — the
        # text-only stand-in for the family logo, since pystray's
        # native menus cannot render images.
        items.append(
            menu_item_class(
                _menu_label(name),
                wrap_fn(change_fn),
                checked=(lambda _item, _active=is_active: _active),
            )
        )

    items.append(menu_separator)
    items.append(
        menu_item_class(
            more_models_text,
            wrap_fn(open_electron_window_fn),
        )
    )
    return items
