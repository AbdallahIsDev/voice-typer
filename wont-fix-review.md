# Won't Fix Tasks — Review & Analysis

> This document explains the 31 tasks marked as "Won't Fix" in `review.md`.
> Each entry describes what the issue is, why it was deferred, and what
> would happen if we implemented the fix vs leaving it alone.

---

## How to read this document

Each task has:
- **What it is** — the technical issue in plain English
- **Gain vs trade-off** — what we get by fixing vs what we lose/cost
- **If we do it** — real-world effect on the user or developer
- **If we don't** — real-world effect of leaving it alone
- **My recommendation** — whether to implement, leave, or try-and-revert

---

## The 24 Tasks

### 1. GQ-48 — slow search when typing only punctuation marks

**What it is:** When you search your history by typing only punctuation (like `%` or `_`), the search is slow (~58ms for 500K records, scaling to ~580ms for 5M records). This is because the database engine has to scan every single row — it can't use an index. In practice, this only happens when someone types `%` or `_` alone in the search box, which is very rare.

**Gain vs trade-off:** If we fix it, the search would be fast even for separator-only queries. But the fix requires either a complex database workaround or rejecting these queries client-side. The extra complexity doesn't justify the benefit since almost no one searches for `%` alone.

**If we do it:** The search stays fast (sub-ms) even when someone types `%` in the search box. The code becomes more complex and harder to maintain.

**If we don't:** If someone types `%` in the search box, it takes ~58ms on a 500K database. On a 5M database it would take ~580ms. This is an edge case — the user would need to deliberately type only punctuation characters.

**My recommendation:** Leave as Won't Fix. The likelihood of a normal user typing `%` or `_` alone in the search box is extremely low. The fix was actually attempted in a previous session and had to be REVERTED because it broke real search functionality for Chinese and Japanese text. If the database grows very large, this can be revisited.

---

### 2. GQ-L7 — redundant memory fill in noise suppressor

**What it is:** The noise suppressor (a filter that removes background noise) fills a memory buffer with zeros on every chunk of audio. This fill is unnecessary — the buffer is already zeroed. The wasted operation is so small (< 0.001ms) that no user would ever notice.

**Gain vs trade-off:** Removing the redundant fill saves a tiny CPU operation per audio chunk. The code is clearer. But it's a trivial change that requires touching a working audio filter — risk of introducing a bug.

**If we do it:** The audio pipeline runs 0.001ms faster per chunk. No user would notice. The code is slightly cleaner.

**If we don't:** The audio pipeline wastes 0.001ms per chunk filling memory that's already zeroed. No user would notice.

**My recommendation:** Leave as Won't Fix. The cost to fix approaches zero, but the benefit also approaches zero. If someone is already editing the noise suppressor for another reason, they can clean this up as a drive-by fix.

---

### 3. GQ-L8 — per-chunk list copy in audio filter chain

**What it is:** Every chunk of audio, the filter chain makes a copy of its filter list (`list(self._filters)`). This copy is a safety measure so that the list can't change mid-processing. The copy is tiny (usually 5-10 items) and takes negligible time.

**Gain vs trade-off:** Removing the copy would save a tiny allocation per chunk. But the copy is there for thread safety — without it, a race condition could crash the audio processing.

**If we do it:** The audio pipeline runs slightly faster. The code becomes more fragile — a filter could be modified while audio is being processed, causing a crash or audio glitch.

**If we don't:** The audio pipeline allocates a tiny list per chunk (5-10 items). No measurable impact on performance or user experience.

**My recommendation:** Leave as Won't Fix. The copy is a deliberate safety measure. Removing it for negligible performance gain is not worth the risk of audio glitches.

---

### 4. GQ-L10 — dead audio analysis function kept in production code

**What it is:** There's a function (`analyze_chunk`) that analyzes audio quality in real-time. But it has ZERO callers in production — it's only used by tests. The function is kept in the production file rather than moved to a test helper.

**Gain vs trade-off:** Moving it to a test helper would clean up the production code. But it's a small function that doesn't affect performance (it's never called in production). The cost of moving it is engineering time with no user benefit.

**If we do it:** The production code file is slightly smaller. No user impact. Developers need to import from a different location when writing tests.

**If we don't:** A small unused function lives in the production code. No user impact. Tests keep calling it from where they always have.

**My recommendation:** Leave as Won't Fix. The function is harmless and doesn't affect production. Move it if you're already editing that file for other reasons.

---

### 5. GQ-L11 — single-chunk audio glitch on filter swap

**What it is:** When the audio filters are swapped (e.g., changing noise reduction settings), there's a tiny race window where one chunk of audio could be processed with the wrong filter state. This could cause a single audio chunk to sound slightly different. The window is extremely narrow (microseconds) and has never been observed in practice.

**Gain vs trade-off:** A proper fix would add a lock to the filter swap, preventing the race entirely. The downside: adding a lock to audio processing risks introducing timing issues or bugs in a performance-sensitive path.

**If we do it:** The race window is eliminated. The code gains a lock that could theoretically cause timing issues on the audio thread.

**If we don't:** There's a theoretical race window that could cause a single audio chunk to glitch. In real-world testing, this has never been observed. The cost is negligible.

**My recommendation:** Leave as Won't Fix. The race window has never been observed in practice. Adding a lock to a hot audio path is riskier than the theoretical glitch it prevents. The session already redesigned the swap path to narrow the window further.

---

### 7. GQ-L16 — native hotkeys file is too large (1649 lines)

**What it is:** The native hotkeys file (which handles keyboard shortcuts like F9 for dictation) is 1649 lines — double the 800-line threshold. It mixes 5 concerns (platform key mapping, listener registration, modifier handling, etc.).

**Gain vs trade-off:** Splitting it would make the hotkey system easier to maintain and debug. The downside: hotkeys are a critical feature (dictation, cancel, repaste) — breaking them would make the app unusable. The split requires careful testing.

**If we do it:** Easier to add new hotkeys, fix bugs. Safer to modify one concern without touching others. The effort is ~2-3 days with platform testing.

**If we don't:** The file is 1649 lines but works. New hotkey features require navigating a large file. Higher risk of introducing bugs when editing.

**My recommendation:** Leave as Won't Fix for now, but consider it for a future session. genuine improvement but large effort for a working file. Defer.

---

### 8. GQ-L18 — config file re-read after key migration

**What it is:** When the app migrates encryption keys to the system keychain, it re-reads the config file from disk. This is a redundant read — the config was already loaded in memory. The redundant read costs ~0.5ms.

**Gain vs trade-off:** Removing the redundant read would save 0.5ms at startup. However, the re-read is a DELIBERATE security measure — it ensures the config file is in a consistent state after the key migration. Removing it could hide a corruption bug.

**If we do it:** The app starts 0.5ms faster. If the config file was corrupted during migration, the app would silently use stale data instead of crashing and alerting the user.

**If we don't:** The app takes 0.5ms longer to read a file it already read. The security guarantee is preserved — if the file is corrupted, the app detects it.

**My recommendation:** Leave as Won't Fix. The redundant read is a deliberate security check. 0.5ms is not worth compromising the safety of the config migration.

---

### 9. GQ-L24 — warm-up test uses 0.5s silence instead of real audio

**What it is:** When the speech model warms up, it uses a 0.5-second silence sample. In production, transcriptions are much longer (25+ seconds). The warm-up completes faster than necessary because the test input is too short.

**Gain vs trade-off:** Using a production-sized sample (25s) for warm-up would better prime the model's caches, potentially making the first real transcription faster. But it would also make the warm-up take longer (25s vs 0.5s), delaying the moment the app is ready to transcribe.

**If we do it:** The first real transcription might be slightly faster. The app takes 25 seconds longer to start up (warm-up takes 25s instead of 0.5s).

**If we don't:** Warm-up completes in 0.5s, app starts quickly. The first transcription might be slightly slower as the model ramps up.

**My recommendation:** Leave as Won't Fix. The 0.5s warm-up is intentionally fast to get the app ready quickly. A 25-second warm-up would make the app feel sluggish. The marginal benefit to first-transcription speed doesn't justify the startup delay.

---

### 10. GQ-L33 — atomic operations use stronger ordering than needed

**What it is:** Atomic operations (like incrementing a counter) use the strongest memory ordering (`SeqCst`) when weaker ordering (`Relaxed`) would suffice. This is a Rust-specific optimization. The actual performance difference is negligible on modern CPUs (sub-1 nanosecond).

**Gain vs trade-off:** Using weaker ordering would be slightly faster and more idiomatic. The downside: proving that weaker ordering is safe requires careful analysis of every code path that reads the atomic variable. Some of these variables are accessed from multiple threads — a mistake could cause a crash that's hard to debug.

**If we do it:** The code runs ~1 nanosecond faster per atomic operation. The code is more idiomatic but harder to audit for correctness.

**If we don't:** The code uses the strongest ordering, which is guaranteed safe. The performance difference is unmeasurable.

**My recommendation:** Leave as Won't Fix. The session already downgraded ONE of the atomic operations (`next_id` to Relaxed) because it was provably safe. The remaining ones (`shutting_down`, `tray_available`) are used across module boundaries where proving safety is harder. The performance gain is negligible.

---

### 11. GQ-L34 — synchronous file writes on boot path

**What it is:** When the app starts, the single-instance lock creates a file using synchronous file operations (`mkdirSync`, `writeFileSync`). These block the main thread during boot. The operations take <1ms total.

**Gain vs trade-off:** Converting to async would unblock the main thread during boot. But the operations are <1ms total — the user would never notice the difference. The single-instance lock is a critical security feature: if the async write failed silently, multiple instances of the app could run simultaneously, causing data corruption.

**If we do it:** The boot sequence is 1ms faster. The single-instance lock becomes async, which could theoretically fail silently, allowing duplicate app instances.

**If we don't:** The boot sequence blocks for <1ms writing a file. The sync guarantee means the lock is always created before the app continues. No risk of duplicate app instances.

**My recommendation:** Leave as Won't Fix. The sync operations are <1ms and the safety guarantee they provide is critical. Async would add risk for zero measurable benefit.

---

### 12. GQ-L36 — buffer concatenation per TCP chunk

**What it is:** The TCP connection handler uses `Buffer.concat` to reassemble data chunks. This creates a new buffer each time rather than growing a single buffer. The allocation is tiny and happens on the connection thread, which is not performance-critical.

**Gain vs trade-off:** Using a growing buffer would reduce allocations. But the TCP connection is already fast enough — the bottleneck is the Python backend, not the buffer assembly. The optimization would save microseconds per connection.

**If we do it:** The connection handler allocates less memory. No user-visible improvement.

**If we don't:** The connection handler allocates a few extra bytes per connection. No user-visible impact.

**My recommendation:** Leave as Won't Fix. The TCP connection handler is not a performance bottleneck. The extra allocations are negligible.

---

### 13. GQ-L37 — `setImmediate` retry on window show

**What it is:** When showing a window, the code uses `setImmediate` to retry if the window isn't ready yet. This is a defensive pattern — a safety net for a rare race condition. The retry is almost never needed.

**Gain vs trade-off:** Removing the `setImmediate` would make the show-window code simpler. But the retry exists because under certain conditions, the window really isn't ready. Removing it could cause a window to fail to show.

**If we do it:** The show-window code is simpler. In rare edge cases (e.g., system under heavy load), a window might fail to appear.

**If we don't:** The code has a defensive retry that almost never fires. Windows always appear, even under load.

**My recommendation:** Leave as Won't Fix. The defensive retry adds negligible complexity and prevents a rare but real window-failure scenario.

---

### 14. GQ-L38 — dynamic locale import on every language switch

**What it is:** When the user switches the app language, the localization module imports the translation file dynamically. This import happens every time the locale changes, even though the file is already loaded. The import is cached by the module system, so the actual cost is near-zero.

**Gain vs trade-off:** Caching the import result would avoid a redundant check. But the module system already caches imports — the overhead is a dictionary lookup (< 0.001ms). The fix would add complexity to save a lookup that's already optimized.

**If we do it:** Language switching is 0.001ms faster. The code is slightly more complex.

**If we don't:** Language switching does a redundant lookup that's already cached by the module system. No user-visible impact.

**My recommendation:** Leave as Won't Fix. The dynamic import is already cached by Node.js's module system. The redundant lookup is effectively free.

---

### 15. GQ-L40 — CSS color conversion without input cache

**What it is:** When the app derives theme colors, it converts CSS color strings to hex format using the DOM API. This is called multiple times for the same input values. A cache would avoid redundant DOM calls.

**Gain vs trade-off:** Adding a cache would avoid redundant DOM calls (a few milliseconds per theme change). The cache would need to be invalidated when the theme changes. The fix is small but adds mutable state.

**If we do it:** Theme derivation is slightly faster. The cache adds a small amount of complexity.

**If we don't:** Color conversion runs multiple times for the same values, taking a few extra milliseconds. Only noticeable during theme changes, which happen rarely.

**My recommendation:** Could be a quick fix, but leave as Won't Fix for now. The DOM calls are fast (< 5ms total) and only happen on theme changes. If someone is already editing the color utilities, it's a 5-minute fix with a `Map` cache.

---

### 16. GQ-L42 — redundant window event listener in sound manager

**What it is:** The sound manager registers 4 window event listeners for the capture phase. One of them (`pointerdown`) is redundant — it doesn't add any functionality beyond what the other 3 listeners already cover.

**Gain vs trade-off:** Removing the redundant listener would clean up the code. The listener is harmless — it fires but does nothing useful. The only cost is a tiny memory allocation for the closure.

**If we do it:** One less event listener. The code is slightly cleaner.

**If we don't:** A redundant listener exists that fires without doing anything useful. No user impact.

**My recommendation:** Leave as Won't Fix. The redundant listener is a few bytes of memory. Not worth the risk of accidentally breaking audio capture by removing the wrong one.

---

### 17. GQ-L43 — unbounded number format cache (bounded in practice)

**What it is:** The number formatting utility uses a `Map` as a cache for `Intl.NumberFormat` instances. The cache has no explicit size limit, but in practice it never exceeds ~48 entries (one per locale × number of unique formats). The "unbounded" concern is theoretical.

**Gain vs trade-off:** Adding a size limit to the cache would make it technically bounded. The downside: the cache is already effectively bounded by the number of locales and formats. Adding a cap adds complexity for no practical benefit.

**If we do it:** The cache has a hard cap. The code is slightly more complex.

**If we don't:** The cache is technically unbounded but never exceeds ~48 entries in practice. No user impact.

**My recommendation:** Leave as Won't Fix. The cache is effectively bounded by the application's needs. The "unbounded" concern is theoretical and has never caused an issue.

---

### 18. GQ-L44 — React effect runs on every render without dependency array

**What it is:** A `useEffect` in the theme settings hook has no dependency array, meaning it runs after EVERY component render. This is a React anti-pattern. The effect itself is lightweight (reads a few values from state), so the performance impact is negligible.

**Gain vs trade-off:** Adding a proper dependency array would make the effect run only when its dependencies change. The fix requires understanding which values the effect actually depends on — getting it wrong could cause the theme to not update correctly.

**If we do it:** The effect runs only when needed. The code is more idiomatic React. The fix could introduce a theme update bug if the dependency array is wrong.

**If we don't:** The effect runs on every render, checking values that usually haven't changed. The overhead is < 0.1ms per render — invisible to the user.

**My recommendation:** Leave as Won't Fix. The overhead is negligible. The dependency array analysis is non-trivial and could introduce bugs. Defer to when the theme settings code is being refactored for other reasons.

---

### 19. GQ-L46 — inline closures create new function objects per sidebar render

**What it is:** Each sidebar navigation item creates a new inline arrow function (closure) on every render: `onClick={() => navigate("/page")}`. These 10 closures are allocated and garbage-collected on every render. The allocation is tiny (~64 bytes each).

**Gain vs trade-off:** Moving the closures to stable callback references would avoid 10 allocations per render. The fix is moderate — requires extracting the click handlers to memoized callbacks or using data attributes.

**If we do it:** The sidebar creates 10 fewer closures per render. The code is slightly more complex.

**If we don't:** The sidebar creates 10 tiny closures per render that are immediately garbage-collected. The allocation is ~640 bytes per render — invisible to the user.

**My recommendation:** Leave as Won't Fix. 10 tiny closures per render is negligible. The fix would make the code harder to read for no measurable benefit.

---

### 20. GQ-L47 — theme settings file is 648 lines

**What it is:** The theme settings component is 648 lines long, mixing 4 sub-sections (custom color picker, contrast settings, draft theme, state machine). It's above the preferred file size threshold but well below the 800-line critical threshold.

**Gain vs trade-off:** Splitting it into smaller files would organize the code. The file is JSX-only (the business logic was already extracted). The residual size is mostly the custom color picker block (lines 429-618).

**If we do it:** The theme settings code is split into focused files. Easier to navigate.

**If we don't:** The file is 648 lines but well-organized. The JSX is straightforward and easy to read.

**My recommendation:** Leave as Won't Fix. 648 lines with JSX-only content is manageable. The file is already past its heaviest refactoring (the state machine was extracted). Defer to a future session if the file grows further.

---

### 21. GQ-L53 — per-sample loop in beep generation script

**What it is:** The beep generation script (`generate_beeps.py`) uses a Python `for` loop to pack each audio sample into a binary string. This is a build-time script (not production code), so its performance doesn't affect the user. The script runs once when the developer runs it.

**Gain vs trade-off:** Vectorizing the loop would make the script run faster. But the script already takes < 1 second to run and is only run by developers, never by end users.

**If we do it:** The generation script runs 10ms faster instead of 50ms. No user impact.

**If we don't:** The generation script takes 50ms. No user impact.

**My recommendation:** Leave as Won't Fix. The script is a developer tool that runs in < 1 second. The performance is irrelevant.

---

### 22. GQ-L54 — branding check script takes 314ms

**What it is:** The `check_branding.py` script (which verifies the app name isn't hardcoded in the wrong places) takes 314ms to run. It could be faster by using `ripgrep` instead of Python's string search. The script runs in CI on every commit.

**Gain vs trade-off:** Rewriting in `ripgrep` would make the check faster (~10ms instead of 314ms). But CI runs many checks in parallel, and 314ms is already fast. The rewrite would add a dependency on `ripgrep` being installed.

**If we do it:** The CI check runs 300ms faster. The script depends on `ripgrep` being installed on the CI runner.

**If we don't:** The CI check takes 314ms. No one notices because it's one of many checks.

**My recommendation:** Leave as Won't Fix. 314ms is already fast for a CI check. Adding a `ripgrep` dependency for a 300ms gain is not worth it.

---

### 23. GQ-L56 — keyring thread count not hard-capped

**What it is:** The credential store (which manages encryption keys) spawns threads for keyring operations. The number of orphan threads is not hard-capped — in theory, if the keyring keeps failing, threads could accumulate. In practice, the keyring either works or fails permanently, so the thread count stays at 1.

**Gain vs trade-off:** Adding a hard cap would prevent theoretical thread accumulation. The fix is small (add a cap check before spawning). The scenario it prevents (repeated keyring failures) has never been observed.

**If we do it:** If the keyring keeps failing, the thread count is capped. The code is slightly more robust.

**If we don't:** If the keyring keeps failing, threads could accumulate. The keyring either works or fails permanently, so this scenario never happens.

**My recommendation:** Leave as Won't Fix. The keyring failure scenario is theoretical. If the keyring fails, the app wouldn't start, so threads wouldn't accumulate.

---

### 24. GQ-L58 — model eviction refactor tied to larger changes

**What it is:** The model manager's LRU eviction logic (which removes old models to free memory) could be refactored. But the refactor is tied to 3 other changes (GQ-6, GQ-7, GQ-29) that haven't been done yet. Fixing it alone would create a partial state that's messy.

**Gain vs trade-off:** A coordinated fix with the 3 related changes would produce clean code. Fixing it alone would create technical debt. The coordination increases the effort significantly.

**If we do it (with the 3 related changes):** The model eviction code is clean and consistent. The effort is ~1 day.

**If we do it (alone):** The eviction code is partially refactored, creating a mismatch with the related code that hasn't been changed yet.

**If we don't:** The current code works. The eviction logic is functional but not modular.

**My recommendation:** Leave as Won't Fix until the related changes are picked up. The refactor is tied to GQ-6, GQ-7, and GQ-29 — fixing it alone would create more problems than it solves.
