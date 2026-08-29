//! Process-wide decoded-tray-icon cache + the whitelisted icon loader.
//!
//! Split out of the former monolithic `tray.rs` (highest-value piece of
//! that decomposition — this is the only part with its own STATE + I/O).
//! Re-exported from `crate::tray` so existing
//! `crate::tray::{load_tray_icon, is_allowed_icon_name}` paths keep
//! resolving.

use std::collections::HashMap;
use std::sync::{Mutex, OnceLock};
use tauri::image::Image;
use tauri::{AppHandle, Manager};

//Process-wide cache of decoded tray icons, keyed by the logical
// name (`"idle"` / `"recording"` / `"transcribing"` / `"error"`).
//
// Without this cache, every `tray_state` event re-read the PNG from
// disk (`resource_dir/icons/tray/<name>.png`) AND re-decoded it via
// `Image::from_path` (which allocates an RGBA buffer + runs the PNG
// decoder). For a typical session the icon flips between `idle` ↔
// `recording` ↔ `transcribing` dozens of times — each flip paid the
// disk-read + decode cost. The cache holds the decoded `Image<'static>`
// (an `Arc`-backed `Cow<[u8]>` internally, so `.clone()` is a single
// atomic increment) and serves subsequent lookups from memory.
//
// `OnceLock<Mutex<HashMap<...>>>` is used instead of a plain
// `OnceLock<HashMap<...>>` because `OnceLock::get_or_init` returns an
// immutable `&T` — we need interior mutability to insert cache-miss
// entries after init. `Mutex` (not `RwLock`) is fine here because the
// cache is read+written under a single short critical section (no I/O
// under the lock — disk read + decode happen BEFORE the lock is taken
// on a cache miss, and the lock is only held for the `HashMap::get` /
// `HashMap::insert`). Tray-state events are low-frequency (a handful
// per session), so even if two threads raced a cache miss on the same
// icon name, both would decode + one `insert` would win — the loser's
// decoded `Image` is dropped (cheap, just an `Arc` decrement).
static TRAY_ICON_CACHE: OnceLock<Mutex<HashMap<String, Image<'static>>>> = OnceLock::new();

/// Predicate that returns `true` iff `name` is one of the four
/// whitelisted tray icon logical names (`idle`, `recording`,
/// `transcribing`, `error`). Used by `load_tray_icon` to defend against
/// a compromised sidecar trying to read an arbitrary file via the tray
/// icon path (e.g. `icon: "../../../etc/passwd"` would otherwise be
/// joined to `resource_dir/icons/tray/../../../etc/passwd.png` and read).
///
/// Extracted from `load_tray_icon` so the whitelist is unit-testable in
/// isolation (calling `load_tray_icon` directly would require a live
/// `AppHandle` + the bundled resource dir — both unavailable in `cargo
/// test`). The test module asserts `is_allowed_icon_name` agrees with
/// the `ALLOWED_ICON_NAMES` test constant below.
///
/// The four names here MUST exactly match the filenames
/// emitted by `voice_typer/client/scripts/generate-icons.mjs` under
/// `src-tauri/icons/tray/` (the icon-generation script writes
/// `{idle,recording,transcribing,error}.png`). A mismatch surfaces as a
/// "tray_state icon not available" warning at runtime — non-fatal but
/// the tray icon stops updating.
pub(crate) fn is_allowed_icon_name(name: &str) -> bool {
    matches!(name, "idle" | "recording" | "transcribing" | "error")
}

/// Map a logical icon name (`"idle"`, `"recording"`,
/// `"transcribing"`, `"error"`) emitted by the Python sidecar to a
/// bundled Tauri image resource. Returns `None` if the name is unknown
/// (caller logs and skips the icon update — non-fatal).
///
/// The icon files live under `src-tauri/icons/tray/` and are declared
/// in `bundle.resources` (string entry `"icons/tray/"` — Tauri
/// preserves the relative path, so the files land at
/// `$RESOURCE/icons/tray/<name>.png`, mirroring the source tree) of
/// the base + per-arch Tauri configs so they're shipped with the
/// bundle. At runtime they're resolved via `app.path().resource_dir()`
/// → `icons/tray/<name>.png`.
///
/// If the icon file is missing on disk (e.g. a fresh dev checkout that
/// hasn't run the icon-generation script), the call returns `None` —
/// the tray icon is left unchanged so the app still runs.
///
/// On a cache miss, the PNG is read + decoded OUTSIDE the cache lock
/// (so a slow disk read doesn't block other threads' cache hits), then
/// inserted under a brief lock. On a cache hit, the cached
/// `Image<'static>` is cloned (cheap — `Arc`-backed `Cow<[u8]>`).
pub(crate) fn load_tray_icon(app: &AppHandle, name: &str) -> Option<Image<'static>> {
    // Whitelist the logical names — never load an arbitrary path from
    // the sidecar (defense against a compromised sidecar trying to read
    // an arbitrary file via the tray icon path).
    if !is_allowed_icon_name(name) {
        log::warn!("[TRAY] ignoring unknown tray_state icon name: {:?}", name);
        return None;
    }
    let allowed = name;

    // Fast path: cache hit. The cache is initialized on first use and
    // populated lazily as each icon name is requested for the first
    // time. `Mutex::lock` is a fast user-space lock (no syscall on the
    // uncontended path); the critical section is a single `HashMap::get`.
    let cache = TRAY_ICON_CACHE.get_or_init(|| Mutex::new(HashMap::new()));
    if let Ok(guard) = cache.lock() {
        if let Some(img) = guard.get(allowed) {
            return Some(img.clone());
        }
    } else {
        // Poisoned lock — fall through to the disk-read path so the
        // tray still updates. The cache is best-effort, not a correctness
        // requirement.
        log::warn!(
            "[TRAY] icon cache lock poisoned — bypassing cache for {:?}",
            allowed
        );
    }

    // Slow path: read + decode from disk. Done OUTSIDE the cache lock
    // so a slow disk doesn't block other threads' cache hits.
    let resource_dir = app.path().resource_dir().ok()?;
    let path = resource_dir
        .join("icons")
        .join("tray")
        .join(format!("{}.png", allowed));
    let bytes = std::fs::read(&path).ok()?;
    let img = match Image::from_path(&path) {
        Ok(img) => img,
        Err(e) => {
            log::warn!(
                "[TRAY] failed to decode tray icon {:?} ({} bytes from {}): {}",
                allowed,
                bytes.len(),
                path.display(),
                e
            );
            return None;
        }
    };

    // Insert into the cache. If another thread raced us and inserted
    // first, our insert is a no-op overwrite with an equivalent value
    // (same logical name → same file → same decoded bytes). The
    // `Mutex` guard is held only for the `HashMap::insert`, not the
    // disk read above.
    if let Ok(mut guard) = cache.lock() {
        guard.insert(allowed.to_string(), img.clone());
    }
    Some(img)
}
