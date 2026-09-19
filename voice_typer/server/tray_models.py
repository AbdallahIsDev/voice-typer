"""Tray model-status presentation helpers."""

import json
import logging
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

# Availability verdicts are owned by the shared
_hf_env_ensured: bool = False

# Qwen's HuggingFace repo id, informational only: the backend
_QWEN_REPO_ID = "andrewleech/qwen3-asr-1.7b-onnx"

# Parakeet's ONNX export repo (fp16, torch-free), informational
_PARAKEET_REPO_ID = "grikdotnet/parakeet-tdt-0.6b-fp16"

# Native tray menus are text-only: pystray's ``MenuItem`` has no image
_FAMILY_MENU_GLYPHS: dict[str, str] = {
    "whisper": "✱",
    "parakeet": "◉",
    "qwen": "⊙",
}

# model name → backend, mirroring the ``candidates`` list in
_MODEL_BACKENDS: dict[str, str] = {
    "tiny": "whisper",
    "large-v3": "whisper",
    "large-v3-turbo": "whisper",
    "parakeet": "parakeet",
    "qwen": "qwen",
}


def _menu_label(name: str) -> str:
    """Return the tray submenu label for a model: family glyph + name."""
    glyph = _FAMILY_MENU_GLYPHS.get(_MODEL_BACKENDS.get(name, ""), "")
    return f"{glyph} {name}" if glyph else name


def more_models_label(localize=None) -> str:
    """Return the label for the trailing "More models..." submenu item."""
    fallback = "More models..."
    if localize is None:
        return fallback
    try:
        text = localize("more_models") or fallback
    except Exception:
        return fallback
    if text == "more_models":
        # Localization table lacks the key (returned the key itself).
        return fallback
    return text


def _ensure_hf_env_once() -> None:
    """run ``asr_setup.ensure_hf_env()`` exactly once per process."""
    global _hf_env_ensured
    if _hf_env_ensured:
        return
    from voice_typer.server.asr_setup import ensure_hf_env

    ensure_hf_env()
    _hf_env_ensured = True


def _check_hf_model_downloaded(repo_id: str, config_dir) -> bool:
    """Return True if the HuggingFace model ``repo_id`` is FULLY downloaded."""
    from voice_typer.server import model_availability

    return model_availability.is_available(repo_id, config_dir)


def _check_qwen_model_downloaded(config_dir, qwen_model_path) -> bool:
    """Return True if the Qwen model WEIGHTS are on disk."""
    if isinstance(qwen_model_path, str) and Path(qwen_model_path).is_dir():
        return True
    from voice_typer.server import model_availability

    return model_availability.is_available(
        _QWEN_REPO_ID, config_dir, extra_watch_dirs=[qwen_model_path] if qwen_model_path else None
    )


def _check_parakeet_model_downloaded(config_dir, parakeet_model_path) -> bool:
    """Return True if the Parakeet model WEIGHTS are on disk."""
    if isinstance(parakeet_model_path, str) and Path(parakeet_model_path).is_dir():
        return True
    from voice_typer.server import model_availability

    return model_availability.is_available(
        _PARAKEET_REPO_ID, config_dir, extra_watch_dirs=[parakeet_model_path] if parakeet_model_path else None
    )


def is_active_model_downloaded(config) -> bool:
    """Return True if the currently-configured ASR model is on disk."""
    # Guard: only probe against a REAL Config. Test doubles
    from voice_typer.server.config import Config as _ConfigCls

    if not isinstance(config, _ConfigCls):
        return True
    from voice_typer.server.config import _config_dir

    config_dir = _config_dir()
    backend = getattr(config, "asr_backend", "whisper") or "whisper"
    from voice_typer.server.model_registry import NO_MODEL_SIZE

    # Qwen / Parakeet are selected via ``asr_backend``, NOT via
    if backend == "qwen":
        return _check_qwen_model_downloaded(config_dir, getattr(config, "qwen_model_path", None))
    if backend == "parakeet":
        return _check_parakeet_model_downloaded(config_dir, getattr(config, "parakeet_model_path", None))
    # Whisper: "No model selected" (``model_size == ""``), there is
    if getattr(config, "model_size", None) == NO_MODEL_SIZE:
        return False
    if backend in ("whisper", "distil-whisper"):
        model_size = getattr(config, "model_size", "") or ""
        from voice_typer.server.model_registry import get_model_metadata

        meta = get_model_metadata(model_size)
        if meta is None:
            # Unknown model size, let the load path surface its own
            return True
        return _check_hf_model_downloaded(meta.repo_id, config_dir)
    # cloud / custom / unknown backend, no local model gate.
    return True


def invalidate_model_availability_cache() -> None:
    """Invalidate the cached model availability checks."""
    from voice_typer.server import model_availability

    model_availability.invalidate()


def build_models_submenu_data(
    config_dir_fn,
    controller_change_model_fn,
    *,
    config_provider=None,
) -> list[tuple[str, bool, bool, Any]]:
    """Gather model info for the tray models submenu.

    Returns a list of tuples: (name, is_downloaded, is_active, change_fn)
    """
    # ensure_hf_env() is process-global idempotent; cache it
    _ensure_hf_env_once()

    # prefer the in-memory Config object over a disk read.
    current_model = "tiny"
    cfg: dict = {}
    qwen_model_path: Any = None
    if config_provider is not None:
        # ``model_size == ""`` (NO_MODEL_SIZE) must stay ``""``, the
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
            log.debug(
                "[TRAY] failed to read config.json for tray menu: %s",
                exc,
                exc_info=True,
            )
            cfg = {}
        current_backend = cfg.get("asr_backend", "whisper") if cfg else "whisper"
        qwen_model_path = cfg.get("qwen_model_path") if cfg else None

    # Models to check, mirrors MODEL_REGISTRY (Whisper family:
    candidates = [
        ("tiny", "whisper", "Systran/faster-whisper-tiny"),
        ("large-v3", "whisper", "Systran/faster-whisper-large-v3"),
        # Turbo lives under mobiuslabsgmbh (faster-whisper _MODELS), not Systran.
        ("large-v3-turbo", "whisper", "mobiuslabsgmbh/faster-whisper-large-v3-turbo"),
        ("parakeet", "parakeet", "nvidia/parakeet-tdt-0.6b-v3"),
        ("qwen", "qwen", None),
    ]

    results = []
    config_dir = config_dir_fn()

    for name, backend, repo_id in candidates:
        downloaded = False
        if backend == "qwen":
            # Qwen is a built-in ONNX backend now (qwen_onnx_model.py —
            downloaded = _check_qwen_model_downloaded(config_dir, qwen_model_path)
        elif repo_id:
            # cached check with 5-second TTL, avoids
            downloaded = _check_hf_model_downloaded(repo_id, config_dir)
        else:
            downloaded = False

        # ``no_model``: with ``model_size == ""`` the user has NO active
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
    open_app_window_fn,
    menu_item_class=None,
    menu_separator=None,
    config_provider=None,
    # localization callable. ``localize("more_models")`` returns
    localize=None,
):
    """#13: Build the full list of pystray MenuItems for the Models submenu."""
    if menu_item_class is None:
        import pystray

        menu_item_class = pystray.MenuItem
    if menu_separator is None:
        import pystray

        menu_separator = pystray.Menu.SEPARATOR

    # prefer the localized label when a localize callable is
    more_models_text = more_models_label(localize)

    items = []
    for name, downloaded, is_active, change_fn in build_models_submenu_data(
        config_dir_fn,
        controller_change_model_fn,
        config_provider=config_provider,
    ):
        if not downloaded:
            continue
        # Native checkmark: pystray's MenuItem ``checked`` parameter
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
            wrap_fn(open_app_window_fn),
        )
    )
    return items
