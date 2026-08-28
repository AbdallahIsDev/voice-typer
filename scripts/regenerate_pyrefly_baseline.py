"""Regenerate pyrefly-baseline.json from the current pyrefly-current.json output.

Preserves metadata keys and updates the _comment to reflect the current count.
"""

import json

with open("pyrefly-current.json", encoding="utf-8") as f:
    current = json.load(f)

with open("pyrefly-baseline.json", encoding="utf-8") as f:
    existing = json.load(f)

new = {"errors": current["errors"]}

metadata_keys = [
    "_comment",
    "_justification",
    "_schema_version",
    "_current_state_2026_07_25_rt_fix_11",
    "_current_state_2026_08_01_oi_16",
    "_current_state_2026_08_05_tk_fix_7",
    "_current_state_2026_08_06_regen",
    "_current_state_2026_08_14_fg_regen",
    "_current_state_2026_08_14_qwen_onnx_regen",
    "_current_state_2026_08_15_autostart_fix",
    "_current_state_2026_08_25_pkg_split_regen",
    "_current_state_2026_08_25_shutdown_split_recon",
]
for k in metadata_keys:
    if k in existing:
        new[k] = existing[k]

old_count = len(existing.get("errors", []))
new_count = len(new["errors"])
print(f"Baseline: {old_count} -> {new_count} errors (delta: {new_count - old_count})")

with open("pyrefly-baseline.json", "w", encoding="utf-8") as f:
    json.dump(new, f, indent=2, ensure_ascii=False)
print("pyrefly-baseline.json written")
