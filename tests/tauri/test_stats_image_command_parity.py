"""Share-stats image export parity pins (MO-121)."""

from __future__ import annotations

import json
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
MAIN_RS = REPO_ROOT / "src-tauri" / "src" / "main.rs"
STATS_IMAGE_RS = REPO_ROOT / "src-tauri" / "src" / "commands" / "system_cmds" / "stats_image.rs"
SYSTEM_CMDS_RS = REPO_ROOT / "src-tauri" / "src" / "commands" / "system_cmds.rs"
WINDOW_NS_TS = (
    REPO_ROOT / "voice_typer" / "client" / "src" / "renderer" / "src" / "lib" / "tauri-bridge" / "window-namespace.ts"
)
LOCALES_DIR = REPO_ROOT / "voice_typer" / "client" / "src" / "main" / "i18n" / "locales"


def _read(path: Path) -> str:
    assert path.is_file(), f"missing source file: {path.relative_to(REPO_ROOT)}"
    return path.read_text(encoding="utf-8")


def test_save_stats_image_is_registered_and_gated() -> None:
    main_rs = _read(MAIN_RS)
    # The registration lives in the generate_handler! list; the import
    assert re.search(r"^\s+save_stats_image,\s*$", main_rs, re.MULTILINE), (
        "save_stats_image must be registered in main.rs's generate_handler! list"
    )
    assert "save_stats_image" in re.sub(
        r"//[^\n]*", "", _read(SYSTEM_CMDS_RS)
    ) or "pub(crate) use stats_image::save_stats_image;" in _read(SYSTEM_CMDS_RS)
    source = _read(STATS_IMAGE_RS)
    assert "require_main_window(&window)?" in source, (
        "save_stats_image must be main-window-only (SEC-026): a compromised "
        "bubble renderer must not write files or open save dialogs"
    )


def test_host_validation_mirrors_the_stats_image_handler() -> None:
    source = _read(STATS_IMAGE_RS)
    # 25 MB data-URL cap (predecessor: MAX_PNG_DATA_URL_BYTES).
    assert "25 * 1024 * 1024" in source
    # Decoded PNG signature check (not just the MIME prefix).
    assert "[0x89, 0x50, 0x4e, 0x47]" in source
    assert "strip_prefix(PNG_DATA_URL_PREFIX)" in source


def test_bridge_installs_save_stats_image() -> None:
    ns = _read(WINDOW_NS_TS)
    assert '"save_stats_image"' in ns, (
        "the Tauri window_ namespace must install saveStatsImage via the "
        "save_stats_image command; without it the hook falls back to an "
        "anchor download (the MO-121 defect)"
    )
    # `copyStatsImage` must stay ABSENT as a bridge method (the web-API
    code = re.sub(r"//[^\n]*", "", ns)
    assert "copyStatsImage" not in code, (
        "copyStatsImage must stay absent (the web-API clipboard path owns "
        "image copies under Tauri; documented in the bridge comment)"
    )


def test_dialog_title_key_stays_in_all_eight_locales() -> None:
    for path in sorted(LOCALES_DIR.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        value = data.get("dialog.export.statsImage")
        assert isinstance(value, str) and value.strip(), (
            f"{path.name} lost the dialog.export.statsImage key; the native "
            "Save-As dialog would fall back to English for that locale"
        )


def test_system_cmds_reexports_the_command() -> None:
    source = _read(SYSTEM_CMDS_RS)
    assert "pub(crate) use stats_image::save_stats_image;" in source
