"""Drift guards for the repeatable Tauri icon regeneration script."""

from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = PROJECT_ROOT / "scripts" / "build" / "generate_tauri_icons.py"

# chunks (C-TEST-5 isolation). ``git show`` reads the object store, NOT


def _git_show_bytes(repo_rel: str) -> bytes:
    """The git-committed bytes for a repo-root-relative path."""
    res = subprocess.run(
        ["git", "show", f"HEAD:{repo_rel}"],
        capture_output=True,
        cwd=str(PROJECT_ROOT),
    )
    if res.returncode != 0:
        raise RuntimeError(
            f"cannot read committed file {repo_rel} via git show: {res.stderr.decode(errors='replace').strip()}"
        )
    return res.stdout


# Read once at import: the committed icon set only changes when HEAD does.
_COMMITTED_ICON_BYTES: dict[str, bytes] = {
    name: _git_show_bytes(f"src-tauri/icons/{name}")
    for name in ("32x32.png", "128x128.png", "128x128@2x.png", "icon.png", "icon.icns", "icon.ico")
}
_COMMITTED_TAURI_CONF_BYTES: bytes = _git_show_bytes("src-tauri/tauri.conf.json")

# The exact committed bundle set (mirrors tauri.conf.json bundle.icon).
BUNDLE_ICONS = {
    "icons/32x32.png",
    "icons/128x128.png",
    "icons/128x128@2x.png",
    "icons/icon.png",
    "icons/icon.icns",
    "icons/icon.ico",
}


def _load_script():
    """Import the script as a module (no side effects, main() is guarded)."""
    spec = importlib.util.spec_from_file_location("_vt_generate_tauri_icons", SCRIPT)
    assert spec is not None and spec.loader is not None, f"cannot load {SCRIPT}"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _make_fake_icons_dir(tmp_path: Path, extras: list[str] | None = None) -> Path:
    """A fake src-tauri/icons/ with the bundle icons + (optional) extras."""
    icons = tmp_path / "icons"
    icons.mkdir()
    for name, data in _COMMITTED_ICON_BYTES.items():
        (icons / name).write_bytes(data)
    (icons / "tray").mkdir()
    for extra in extras or []:
        if extra.endswith("/"):
            # Realistic: tauri icon's android/ios trees are NESTED.
            nested = icons / extra.rstrip("/")
            nested.mkdir()
            (nested / "mipmap-hdpi" / "ic_launcher.png").parent.mkdir()
            (nested / "mipmap-hdpi" / "ic_launcher.png").write_bytes(b"x")
        else:
            (icons / extra).write_bytes(b"x")
    return icons


def test_script_exists_and_documents_the_pipeline() -> None:
    """The script must exist and document regen → prune → drift-guard."""
    assert SCRIPT.is_file(), f"missing: {SCRIPT}"
    text = SCRIPT.read_text(encoding="utf-8")
    assert "tauri icon" in text, "script must invoke `tauri icon`"
    assert "logo.svg" in text, "script must reference the logo source of truth"
    assert "bundle.icon" in text, "script must prune to the bundle.icon set"
    assert "test_tauri_conf_icon_list_matches_tracked_icons" in text, (
        "script must re-run the config ↔ git drift guard after regeneration"
    )
    assert "generate_icon.py" in text, "script must document it is distinct from the legacy generate_icon.py"


def test_bundle_icon_paths_matches_committed_set(tmp_path) -> None:
    """Reading tauri.conf.json yields exactly the 6 committed bundle icons."""
    mod = _load_script()
    conf = tmp_path / "tauri.conf.json"
    conf.write_bytes(_COMMITTED_TAURI_CONF_BYTES)
    assert mod.bundle_icon_paths(conf) == BUNDLE_ICONS


def test_prune_removes_tauri_icon_extras_keeps_bundle_set(tmp_path) -> None:
    """The prune deletes tauri icon's superset and keeps the bundle + tray."""
    mod = _load_script()
    icons = _make_fake_icons_dir(
        tmp_path,
        extras=["64x64.png", "Square30x30Logo.png", "StoreLogo.png", "android/", "ios/"],
    )
    pruned = mod.prune_icons_dir(icons, BUNDLE_ICONS)
    assert set(pruned) == {"64x64.png", "Square30x30Logo.png", "StoreLogo.png", "android", "ios"}
    remaining = {p.name for p in icons.iterdir()}
    assert remaining == {"32x32.png", "128x128.png", "128x128@2x.png", "icon.png", "icon.icns", "icon.ico", "tray"}
    missing, extra = mod.validate_icon_set(icons, BUNDLE_ICONS)
    assert missing == [] and extra == []


def test_validate_icon_set_catches_missing_and_extra(tmp_path) -> None:
    """Validation reports a dropped bundle icon and an unpruned extra."""
    mod = _load_script()
    icons = _make_fake_icons_dir(tmp_path, extras=["64x64.png"])
    (icons / "icon.icns").unlink()  # simulate a future tauri icon dropping it
    missing, extra = mod.validate_icon_set(icons, BUNDLE_ICONS)
    assert missing == ["icon.icns"]
    assert extra == ["64x64.png"]


def test_prune_is_idempotent(tmp_path) -> None:
    """Re-running the prune after a clean set changes nothing."""
    mod = _load_script()
    icons = _make_fake_icons_dir(tmp_path, extras=["64x64.png", "android/"])
    mod.prune_icons_dir(icons, BUNDLE_ICONS)
    before = sorted(p.name for p in icons.iterdir())
    pruned = mod.prune_icons_dir(icons, BUNDLE_ICONS)
    assert pruned == []
    assert sorted(p.name for p in icons.iterdir()) == before


def test_compare_icon_trees_in_sync_returns_no_problems(tmp_path) -> None:
    """Identical regenerated + committed trees report no drift."""
    mod = _load_script()
    committed = _make_fake_icons_dir(tmp_path)
    regen_root = tmp_path / "regen"
    regen_root.mkdir()
    regen = _make_fake_icons_dir(regen_root)
    problems = mod.compare_icon_trees(regen, committed, BUNDLE_ICONS, icns_validator=lambda _p: [])
    assert problems == []


def test_compare_icon_trees_reports_byte_drift(tmp_path) -> None:
    """A changed deterministic file (PNG/ICO) is reported as drift."""
    mod = _load_script()
    committed = _make_fake_icons_dir(tmp_path)
    regen_root = tmp_path / "regen"
    regen_root.mkdir()
    regen = _make_fake_icons_dir(regen_root)
    (regen / "32x32.png").write_bytes(b"PNG-changed")
    problems = mod.compare_icon_trees(regen, committed, BUNDLE_ICONS, icns_validator=lambda _p: [])
    assert any("32x32.png" in p and "differs" in p for p in problems), problems


def test_compare_icon_trees_reports_missing_and_extra(tmp_path) -> None:
    """A dropped committed icon and an unpruned extra are both reported."""
    mod = _load_script()
    committed = _make_fake_icons_dir(tmp_path)
    regen_root = tmp_path / "regen"
    regen_root.mkdir()
    regen = _make_fake_icons_dir(regen_root)
    (regen / "icon.icns").unlink()  # regen dropped it -> missing from regen set
    (regen / "64x64.png").write_bytes(b"x")  # unpruned extra
    problems = mod.compare_icon_trees(regen, committed, BUNDLE_ICONS, icns_validator=lambda _p: [])
    assert any("missing from regenerated set" in p and "icon.icns" in p for p in problems), problems
    assert any("64x64.png" in p and "not committed" in p for p in problems), problems


def test_compare_icon_trees_validates_icns_structurally_not_by_bytes(tmp_path) -> None:
    """ICNS is compared structurally; different bytes are NOT drift, an"""
    mod = _load_script()
    committed = _make_fake_icons_dir(tmp_path)
    regen_root = tmp_path / "regen"
    regen_root.mkdir()
    regen = _make_fake_icons_dir(regen_root)
    # Different-but-valid icns bytes on both sides -> no byte problem.
    (regen / "icon.icns").write_bytes(b"icns-valid-a")
    (committed / "icon.icns").write_bytes(b"icns-valid-b")
    problems = mod.compare_icon_trees(
        regen,
        committed,
        BUNDLE_ICONS,
        icns_validator=lambda _p: [],
    )
    assert not any("differs" in p for p in problems), problems
    # An invalid committed icns -> reported via the structural validator.
    problems = mod.compare_icon_trees(
        regen,
        committed,
        BUNDLE_ICONS,
        icns_validator=lambda p: ["invalid icns"] if p.read_bytes() == b"icns-valid-b" else [],
    )
    assert any("invalid icns" in p for p in problems), problems


def test_check_mode_is_registered() -> None:
    """The script must expose the ``--check`` CI drift mode."""
    mod = _load_script()
    text = SCRIPT.read_text(encoding="utf-8")
    assert '"--check" in sys.argv' in text, "script must dispatch --check"
    assert callable(mod.check_committed_icons), "check_committed_icons must exist"
    assert callable(mod.compare_icon_trees), "compare_icon_trees must exist"
