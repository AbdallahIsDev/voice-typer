"""Version-migration helpers for the ``Config`` dataclass."""

from __future__ import annotations

import logging
import os
import time
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover, typing-only, never imported at runtime
    from voice_typer.server.config import Config

log = logging.getLogger("voice_typer.server.config")


def _backup_before_downgrade_impl(
    cls: type[Config],
    data: dict[str, Any],
    loaded_version: Any,
    config_file,
) -> None:
    """best-effort versioned backup when an older build loads a"""
    import voice_typer.server.config as _cfg

    if not isinstance(loaded_version, int):
        return
    # embed schema version + epoch seconds + PID +
    ts_sec = int(time.time())
    pid = os.getpid()
    ts_ns = time.time_ns() % 1_000_000
    versioned_bak = config_file.parent / f"config.json.v{loaded_version}-{ts_sec}-{pid}-{ts_ns}.bak"
    # use the secure read/write helpers (O_NOFOLLOW + atomic
    try:
        raw_text = _cfg._secure_read_text(config_file)
        _cfg._secure_atomic_write(versioned_bak, raw_text)
        log.warning(
            "[CONFIG] downgraded build loaded newer config schema_version=%d "
            "(supported=%d); backed up original to %s before any save can overwrite",
            loaded_version,
            _cfg._CURRENT_SCHEMA_VERSION,
            versioned_bak,
        )
        data.setdefault("_load_warnings", []).append(
            f"Config file schema_version={loaded_version} is newer than this build "
            f"supports ({_cfg._CURRENT_SCHEMA_VERSION}). Unknown fields were dropped from "
            f"the in-memory config. The original file was backed up to "
            f"{versioned_bak.name} before any save can overwrite it, restore this "
            f"file manually after upgrading to a newer build."
        )
    except (OSError, ValueError) as e:
        # SEC-002 inode-changed-during-read guard (symlink TOCTOU
        log.warning(
            "[CONFIG] failed to back up newer-version config to %s before downgrade save: %s",
            versioned_bak,
            e,
        )
        data.setdefault("_load_warnings", []).append(
            f"Config file schema_version={loaded_version} is newer than this build "
            f"supports ({_cfg._CURRENT_SCHEMA_VERSION}). Unknown fields were dropped. "
            f"WARNING: backup of the original file failed ({e}), downgrading and "
            f"saving will irrecoverably lose the higher-version fields."
        )
        return
    # cap retained versioned-downgrade backups to 3
    try:
        _cfg._prune_kept_backups(
            config_file.parent,
            prefix="config.json.v",
            keep=3,
        )
    except OSError as prune_exc:
        log.debug(
            "[CONFIG] failed to prune old versioned-downgrade backups: %s",
            prune_exc,
        )
