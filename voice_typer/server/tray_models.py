"""Extracted models submenu logic from tray.py.

ARCH-007: _build_models_submenu data enumeration was previously inline
in TrayIcon.  The data-gathering logic is now a standalone function
so it can be tested independently and potentially shared.
"""

import json
import logging
from typing import Any, Optional

log = logging.getLogger(__name__)


def build_models_submenu_data(config_dir_fn, controller_change_model_fn) -> list[tuple[str, bool, bool, Any]]:
    """Gather model info for the tray models submenu.

    Returns a list of tuples: (name, is_downloaded, is_active, change_fn)

    Parameters:
        config_dir_fn: callable returning the config directory Path
        controller_change_model_fn: callable(name) to change the active model
    """
    from voice_typer.server.asr_setup import ensure_hf_env
    ensure_hf_env()

    # Read current model from config
    config_path = config_dir_fn() / "config.json"
    current_model = "tiny.en"
    cfg: dict = {}
    try:
        with open(config_path, "r") as f:
            cfg = json.load(f)
        current_model = cfg.get("model_size", "tiny.en")
    except Exception:
        pass

    # Models to check
    candidates = [
        ("tiny.en", "whisper", "Systran/faster-whisper-tiny.en"),
        ("small.en", "whisper", "Systran/faster-whisper-small.en"),
        ("medium.en", "whisper", "Systran/faster-whisper-medium.en"),
        ("parakeet", "parakeet", "nvidia/parakeet-tdt-0.6b-v3"),
        ("qwen", "qwen", None),
    ]

    results = []
    current_backend = cfg.get("asr_backend", "whisper") if cfg else "whisper"

    for name, backend, repo_id in candidates:
        downloaded = False
        if backend == "qwen":
            try:
                import qwen_asr  # noqa
                downloaded = True
            except ImportError:
                pass
        elif repo_id:
            cache_dir = config_dir_fn() / "huggingface" / "hub"
            ref_file = cache_dir / f"models--{repo_id.replace('/', '--')}" / "refs" / "main"
            downloaded = ref_file.exists()
        else:
            downloaded = False

        is_active = (
            (name == current_model and current_backend == backend)
            or (name == "parakeet" and current_backend == "parakeet")
            or (name == "qwen" and current_backend == "qwen")
        )

        results.append((name, downloaded, is_active, lambda n=name: controller_change_model_fn(n)))

    return results
