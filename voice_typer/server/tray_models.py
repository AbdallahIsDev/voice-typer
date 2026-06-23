"""Extracted models submenu logic from tray.py.

ARCH-007: _build_models_submenu data enumeration was previously inline
in TrayIcon.  The data-gathering logic is now a standalone function
so it can be tested independently and potentially shared.

NEW-PERF-004: previously every menu rebuild (every right-click on the
tray icon) called ``ensure_hf_env()``, did a fresh ``import qwen_asr``
(~50–150 ms), and ran 5+ filesystem ``exists()`` checks.  This caused
noticeable menu-open lag.  We now cache:

- The qwen_asr import availability (it never changes mid-session).
- The HuggingFace hub ``refs/main`` existence check (with a 5-second
  TTL so a download started in the Models page is reflected within
  5 seconds without making the user wait on every right-click).
"""

import json
import logging
import time
from typing import Any, Optional

log = logging.getLogger(__name__)

# NEW-PERF-004: caches for the model availability checks.
# These are module-level because the tray menu is rebuilt on every
# right-click and the underlying data (qwen_asr installed; HF model
# downloaded) changes very rarely — only when the user installs a
# new package or finishes a model download.
_qwen_asr_available_cache: "bool | None" = None
_qwen_asr_cache_checked: bool = False
# Cache of (repo_id, config_dir) → (downloaded, timestamp).
# TTL is 5 seconds: long enough to avoid per-right-click stat() calls,
# short enough that a download finishing in the Models page is
# reflected on the next right-click within 5 seconds.
_HF_DOWNLOAD_CACHE_TTL_SECONDS = 5.0
_hf_download_cache: "dict[tuple[str, str], tuple[bool, float]]" = {}


def _check_qwen_asr_available() -> bool:
    """Return True if qwen_asr is importable.  Cached for the session.

    NEW-PERF-004: the ``import qwen_asr`` statement takes 50–150 ms
    because qwen_asr pulls in heavy ML deps.  Doing this on every
    tray right-click caused noticeable menu-open lag.  The result
    never changes mid-session (you'd have to pip install/uninstall
    qwen_asr), so we cache it after the first check.
    """
    global _qwen_asr_available_cache, _qwen_asr_cache_checked
    if _qwen_asr_cache_checked:
        return bool(_qwen_asr_available_cache)
    try:
        import qwen_asr  # noqa: F401
        _qwen_asr_available_cache = True
    except ImportError:
        _qwen_asr_available_cache = False
    _qwen_asr_cache_checked = True
    return bool(_qwen_asr_available_cache)


def _check_hf_model_downloaded(repo_id: str, config_dir) -> bool:
    """Return True if the HuggingFace model ``repo_id`` is downloaded.

    NEW-PERF-004: cached for 5 seconds so a tray right-click doesn't
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


def invalidate_model_availability_cache() -> None:
    """Invalidate the cached model availability checks.

    Called by the model download path (Models page) so the next tray
    right-click reflects the newly-downloaded model immediately,
    without waiting for the TTL to expire.
    """
    global _qwen_asr_available_cache, _qwen_asr_cache_checked
    _qwen_asr_available_cache = None
    _qwen_asr_cache_checked = False
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
        config_provider: optional live Config object. ARCH-037: when provided,
            uses ``config_provider.asr_backend`` / ``config_provider.model_size``
            instead of re-parsing config.json from disk. Falls back to disk
            read when None.
    """
    from voice_typer.server.asr_setup import ensure_hf_env
    ensure_hf_env()

    # ARCH-037: prefer the in-memory Config object over a disk read.
    # Falls back to disk read when config_provider is None (e.g. tests).
    current_model = "tiny.en"
    cfg: dict = {}
    if config_provider is not None:
        current_model = getattr(config_provider, "model_size", "tiny.en") or "tiny.en"
        current_backend = getattr(config_provider, "asr_backend", "whisper") or "whisper"
    else:
        # Read current model from config.json on disk.
        config_path = config_dir_fn() / "config.json"
        try:
            with open(config_path, "r") as f:
                cfg = json.load(f)
            current_model = cfg.get("model_size", "tiny.en")
        except Exception:
            pass
        current_backend = cfg.get("asr_backend", "whisper") if cfg else "whisper"

    # Models to check
    candidates = [
        ("tiny.en", "whisper", "Systran/faster-whisper-tiny.en"),
        ("small.en", "whisper", "Systran/faster-whisper-small.en"),
        ("medium.en", "whisper", "Systran/faster-whisper-medium.en"),
        ("parakeet", "parakeet", "nvidia/parakeet-tdt-0.6b-v3"),
        ("qwen", "qwen", None),
    ]

    results = []
    config_dir = config_dir_fn()

    for name, backend, repo_id in candidates:
        downloaded = False
        if backend == "qwen":
            # NEW-PERF-004: cached check — avoids 50–150 ms import
            # on every right-click.
            downloaded = _check_qwen_asr_available()
        elif repo_id:
            # NEW-PERF-004: cached check with 5-second TTL — avoids
            # 5× filesystem exists() per right-click.
            downloaded = _check_hf_model_downloaded(repo_id, config_dir)
        else:
            downloaded = False

        is_active = (
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
        config_provider: optional live Config object. ARCH-037: when provided,
            the data builder uses ``config_provider.asr_backend`` /
            ``config_provider.model_size`` instead of re-parsing config.json
            from disk. Falls back to disk read when None.
    """
    if menu_item_class is None:
        import pystray
        menu_item_class = pystray.MenuItem
    if menu_separator is None:
        import pystray
        menu_separator = pystray.Menu.SEPARATOR

    items = []
    for name, downloaded, is_active, change_fn in build_models_submenu_data(
        config_dir_fn, controller_change_model_fn, config_provider=config_provider,
    ):
        if not downloaded:
            continue
        items.append(
            menu_item_class(
                f"{'• ' if is_active else '  '}{name}",
                wrap_fn(change_fn),
            )
        )

    items.append(menu_separator)
    items.append(
        menu_item_class(
            "More models...",
            wrap_fn(open_electron_window_fn),
        )
    )
    return items
