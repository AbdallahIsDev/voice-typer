"""Fast-moving extractor refresh state (ADR-0023 background updates).

yt-dlp and solver-script freshness is version/date metadata only. The
offline pack remains the heavy software boundary; this module never
downloads executable code.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

STATE_FILENAME = "media-extractor.json"
REMOTE_FILENAME = "media-extractor.json"


@dataclass(frozen=True)
class ExtractorRefreshState:
    """Freshness metadata for the user-initiated extractor path."""

    backend: str
    backend_version: str | None
    solver_version: str | None
    checked_at: int
    update_available: bool = False
    remote_backend_version: str | None = None
    remote_solver_version: str | None = None


def installed_extractor_versions() -> tuple[str | None, str | None]:
    """Return installed ``(yt-dlp, solver)`` versions, or Nones if absent."""
    try:
        import yt_dlp
    except ImportError:
        return (None, None)

    version = getattr(yt_dlp, "version", None) or getattr(yt_dlp, "__version__", None)
    solver = None
    try:
        import yt_dlp_ejs  # type: ignore

        solver = getattr(yt_dlp_ejs, "version", None) or getattr(yt_dlp_ejs, "__version__", None)
    except ImportError:
        solver = None
    return (str(version) if version else None, str(solver) if solver else None)


def state_path(root: Path | str | None = None) -> Path:
    """Return the JSON freshness-state path for media extraction."""
    if root is not None:
        return Path(root) / STATE_FILENAME
    from voice_typer.server.service.offline_pack import _default_offline_pack_root

    return _default_offline_pack_root() / STATE_FILENAME


def load_state(path: Path | str | None = None) -> ExtractorRefreshState | None:
    """Load persisted refresh metadata, returning None when unusable."""
    target = Path(path) if path is not None else state_path()
    try:
        raw = target.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    try:
        return ExtractorRefreshState(
            backend=str(data.get("backend") or "yt-dlp"),
            backend_version=data.get("backend_version"),
            solver_version=data.get("solver_version"),
            checked_at=int(data.get("checked_at") or 0),
            update_available=bool(data.get("update_available", False)),
            remote_backend_version=data.get("remote_backend_version"),
            remote_solver_version=data.get("remote_solver_version"),
        )
    except (TypeError, ValueError):
        return None


def save_state(state: ExtractorRefreshState, path: Path | str | None = None) -> bool:
    """Persist refresh metadata atomically; False when unwritable."""
    target = Path(path) if path is not None else state_path()
    payload = {
        "backend": state.backend,
        "backend_version": state.backend_version,
        "solver_version": state.solver_version,
        "checked_at": state.checked_at,
        "update_available": state.update_available,
        "remote_backend_version": state.remote_backend_version,
        "remote_solver_version": state.remote_solver_version,
    }
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        tmp.replace(target)
    except OSError:
        log.debug("[MEDIA] extractor state save failed", exc_info=True)
        return False
    return True


def check_refresh(
    *,
    http_get: Callable[..., str] | None = None,
    manifest_url: str | None = None,
    local_backend: str | None = None,
    local_solver: str | None = None,
    state_file: Path | str | None = None,
    now: int | None = None,
) -> ExtractorRefreshState:
    """Compare installed extractor versions against a remote manifest."""
    from voice_typer.server.service import update_check

    installed_backend, installed_solver = installed_extractor_versions()
    backend = local_backend if local_backend is not None else installed_backend
    solver = local_solver if local_solver is not None else installed_solver
    checked = now if now is not None else int(time.time() * 1000)
    state = ExtractorRefreshState(
        backend="yt-dlp",
        backend_version=backend,
        solver_version=solver,
        checked_at=checked,
    )

    url = manifest_url or _remote_url()
    get = http_get or update_check._http_get_manifest
    try:
        body = get(url, max_bytes=64 * 1024, timeout=30.0)
        data = json.loads(body)
    except Exception:  # noqa: BLE001, freshness is advisory
        log.debug("[MEDIA] extractor refresh check failed", exc_info=True)
        save_state(state, state_file)
        return state
    if not isinstance(data, dict):
        save_state(state, state_file)
        return state

    remote_backend = data.get("yt_dlp_version") or data.get("backend_version")
    remote_solver = data.get("solver_version") or data.get("ejs_version")
    available = _is_newer(remote_backend, backend) or _is_newer(remote_solver, solver)
    state = ExtractorRefreshState(
        backend="yt-dlp",
        backend_version=backend,
        solver_version=solver,
        checked_at=checked,
        update_available=available,
        remote_backend_version=str(remote_backend) if remote_backend else None,
        remote_solver_version=str(remote_solver) if remote_solver else None,
    )
    save_state(state, state_file)
    return state


def _remote_url() -> str:
    from voice_typer.server.branding import APP_REPO

    return f"https://github.com/{APP_REPO}/releases/latest/download/{REMOTE_FILENAME}"


def _is_newer(remote: object, local: str | None) -> bool:
    if not isinstance(remote, str) or not remote or not local:
        return False
    from voice_typer.server.service.update_check import is_newer_version

    try:
        return is_newer_version(remote, local)
    except Exception:  # noqa: BLE001, malformed versions never trigger updates
        return False
