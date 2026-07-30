"""Voice template manager: CRUD, match/expand, variable substitution.

Templates are trigger-phrase → output-text pairs stored in a JSON file.
When the user says a trigger phrase during dictation, the system replaces
the transcribed text with the stored output.

Pipeline order: transcribe → text cleanup → vocabulary → template match → auto-punctuate → paste

Variables supported in output text:
    {today}     — current date (e.g., "2026-06-03")
    {now}       — current time (e.g., "14:30")
    {clipboard} — current clipboard content
    {username}  — system username
"""

import getpass
import json
import logging
import re
import threading
from datetime import datetime
from pathlib import Path

log = logging.getLogger(__name__)

# ER-55: precompiled regexes — was `re.sub(r"\s+", ...)` recompiled per call
# (Python's re module has an internal cache, but with MAX_TEMPLATES=1000 the
# inner loop re-looks-up the cached pattern 1000 times per dictation).
_WHITESPACE_RE = re.compile(r"\s+")
# ER-55: single regex pass for variable substitution with lazy resolution.
# Was: 4 eager str.replace() calls, including a potentially-blocking
# _get_clipboard_text() even when the output had no {clipboard} placeholder.
_TEMPLATE_VAR_RE = re.compile(r"\{(today|now|clipboard|username)\}")

TEMPLATES_FILENAME = "voice-typer-templates.json"

# G4-M-38: SEC-011-style caps for templates to prevent resource
# exhaustion. Mirror vocabulary.MAX_CORRECTIONS_ENTRIES pattern.
MAX_TEMPLATES = 1000
MAX_TRIGGER_LENGTH = 200
MAX_OUTPUT_LENGTH = 2000

# ─── Variable substitution ─────────────────────────────────────────────


def _get_clipboard_text() -> str:
    """Try to read current clipboard content."""
    try:
        import pyperclip

        text = pyperclip.paste()
        return str(text) if text and isinstance(text, str) else ""
    except Exception:
        return ""


def substitute_variables(text: str) -> str:
    """Replace template variables with their current values.

    Supported variables:
        {today}     — date in YYYY-MM-DD
        {now}       — time in HH:MM
        {clipboard} — current clipboard content
        {username}  — OS username

    ER-55: single regex pass with lazy variable resolution. The old code
    eagerly computed all 4 values (including a potentially-blocking
    _get_clipboard_text() call) even when the output text contained none
    of the variables. Now each variable is resolved only when its
    placeholder is actually present, and datetime.now() is called at most
    once (shared between {today} and {now}).
    """
    if "{" not in text:
        # Fast path: no placeholders at all.
        return text
    # Lazy: only fetch when the placeholder is present.
    _now: datetime | None = None

    def _resolve(match: re.Match) -> str:
        nonlocal _now
        var = match.group(1)
        if var == "today":
            if _now is None:
                _now = datetime.now()
            return _now.strftime("%Y-%m-%d")
        if var == "now":
            if _now is None:
                _now = datetime.now()
            return _now.strftime("%H:%M")
        if var == "clipboard":
            return _get_clipboard_text()
        # username
        return _safe_getuser()

    return _TEMPLATE_VAR_RE.sub(_resolve, text)


def _safe_getuser() -> str:
    """Get username safely, returning 'user' on any failure.

    NEW-CQ-013: ``getpass.getuser()`` always returns ``str`` (or raises).
    The previous ``isinstance(name, str)`` check was dead code. Simplified
    to a direct truthiness check.
    """
    try:
        name = getpass.getuser()
        return name if name else "user"
    except Exception:
        return "user"


# ─── Template manager ──────────────────────────────────────────────────


class TemplateManager:
    """Manages voice templates: CRUD, persistence, matching."""

    def __init__(self, config_dir: Path | None = None):
        if config_dir is None:
            from voice_typer.server.config import _config_dir

            config_dir = _config_dir()
        self._path = config_dir / TEMPLATES_FILENAME
        # Route persistence through PersistedJSON so templates
        # get single-slot .bak before overwrite + corrupt-file
        # quarantine + 0o600 perms (parity with config.py). The
        # previous implementation used _secure_atomic_write for saves
        # but had NO .bak and NO quarantine on load failure.
        from voice_typer.server.secure_file_io import PersistedJSON

        self._store = PersistedJSON(self._path, default={"templates": []})
        self._templates: list[dict] = []
        # XZ-R11-06: re-entrant lock guarding ``_templates`` +
        # ``_exact_index`` + ``_contains_list``.  ``match`` iterates
        # the indexes while CRUD methods (``add`` / ``update`` /
        # ``delete`` / ``import_json``) mutate ``_templates`` and
        # rebuild the indexes via ``_rebuild_indexes``.  Without a
        # lock, a CRUD mutation interleaved with a ``match`` iteration
        # could observe a half-rebuilt index — the same race CR-23
        # fixed for ``VocabularyManager``.  ``RLock`` because ``_save``
        # is called from inside already-locked CRUD methods and
        # ``add``'s rollback path re-mutates ``_templates``.
        self._lock = threading.RLock()
        # S5-CR-60: match indexes for O(1) exact lookup + reduced-scan
        # contains lookup. Rebuilt by ``_rebuild_indexes`` after every
        # mutation (add/update/delete/import/load). Pre-fix ``match`` did
        # an O(N) linear scan of ``self._templates`` on every dictation;
        # with MAX_TEMPLATES=1000 that was 1000 iterations per call.
        self._exact_index: dict[str, dict] = {}
        self._contains_list: list[tuple[str, dict]] = []
        # S5-CR-60: _load() calls _rebuild_indexes() at its end so the
        # indexes are populated by the time __init__ returns.
        self._load()

    # ── Match indexes (S5-CR-60) ─────────────────────────────────────

    def _rebuild_indexes(self) -> None:
        """S5-CR-60: rebuild the match indexes from ``self._templates``.

        Called after ``_load`` and after every mutation (add/update/
        delete/import). The indexes let ``match`` do an O(1) dict lookup
        for exact-mode templates and a reduced-scan linear search over
        ONLY contains-mode templates (sorted by trigger length ascending
        so the early-exit in ``match`` is safe).

        Behavior preservation:
        - Exact mode: only one template can match a given input (the one
          whose normalized trigger equals the normalized input). If two
          templates share a normalized trigger, the FIRST one in
          ``self._templates`` order wins (we skip the duplicate insert),
          matching the pre-fix linear scan's strict ``<`` comparison.
        - Contains mode: the list is sorted by trigger length ascending.
          Python's ``sort`` is stable, so templates at the same length
          preserve their original order — matching the pre-fix behavior
          where the first template at the shortest matching length wins.
        - Cross-mode: the docstring contract "shortest trigger wins when
          multiple templates match" is preserved by checking the exact
          index first (setting the upper-bound length) then scanning
          contains templates strictly shorter than that bound.
        """
        self._exact_index = {}
        self._contains_list = []
        for t in self._templates:
            trigger = t.get("trigger", "")
            if not trigger:
                continue
            trigger_norm = _WHITESPACE_RE.sub(" ", trigger.strip()).lower()
            mode = t.get("match_mode", "exact")
            if mode == "contains":
                self._contains_list.append((trigger_norm, t))
            else:
                # Exact: first-wins for duplicate normalized triggers
                # (preserves the pre-fix linear scan's first-match-wins
                # behavior under strict ``<`` comparison).
                if trigger_norm not in self._exact_index:
                    self._exact_index[trigger_norm] = t
        # Sort contains list by trigger length ascending so ``match``
        # can early-exit once it sees a trigger >= the current best
        # length. Stable sort preserves original order for same-length
        # triggers (first-wins semantics).
        self._contains_list.sort(key=lambda pair: len(pair[0]))

    @property
    def templates(self) -> list[dict]:
        """Public read accessor for the templates list.

        XS-15: the previous public ``templates`` attribute was renamed
        to ``_templates`` (private) without a property shim, breaking
        every caller — tests, IPC handlers, the tray menu builder, and
        the on-disk persistence round-trip in
        ``tests/test_history_and_models.py::TestTemplatesPersistToDisk``.

        Returns a SHALLOW COPY of the underlying list so callers can
        iterate / index / clear the returned list without mutating the
        manager's internal state (per the
        ``test_templates_property_returns_copy`` contract: modifying the
        returned object must not affect the manager).

        The individual template dicts inside the list are NOT copied
        (shallow copy) — callers that need to mutate a template should
        use :meth:`update` so the change persists to disk.

        XZ-R11-06: copies under the lock so a concurrent CRUD mutation
        can't observe a half-updated list (e.g. ``_templates.pop``
        mid-iteration by ``delete``).
        """
        with self._lock:
            return list(self._templates)

    # ── Persistence ──────────────────────────────────────────────────

    def _load(self) -> None:
        """Load templates from JSON file.

        Persistence is routed through :class:`PersistedJSON`
        (``self._store``). On parse failure (corrupt JSON, OSError,
        symlink-TOCTOU raise), the helper quarantines the corrupt file
        to ``<path>.corrupt-<ts>`` for forensic recovery and returns
        the configured default. The previous implementation silently
        fell back to an empty list with a single WARNING log line — no
        quarantine — so the next ``_save`` would atomically overwrite
        the corrupt file with defaults, destroying any chance of
        forensic recovery. Mirrors ``config.py:1744-1763`` and
        ``crash_recovery.py:186-219``.

        SEC-audit-006 (Round 0 forward-port): the underlying read
        uses :func:`voice_typer.server.config._secure_read_text`
        (POSIX ``O_NOFOLLOW`` + inode re-verification) to prevent a
        symlink-TOCTOU attack where an attacker replaces the templates
        file with a symlink to a sensitive file (e.g.
        ``~/.ssh/id_rsa``).

        XZ-R11-06: ``_load`` is called only from ``__init__`` (before
        the instance is published to other threads), so it does NOT
        acquire ``self._lock`` — the lock guards public-method
        interleaving, not single-threaded construction.
        """
        data = self._store.load()
        if isinstance(data, list):
            self._templates = data
        elif isinstance(data, dict) and "templates" in data:
            self._templates = data["templates"]
        else:
            self._templates = []
        # S5-CR-60: rebuild match indexes after load.
        self._rebuild_indexes()
        log.info("[TEMPLATES] Loaded %d templates from %s", len(self._templates), self._path)

    def _save(self) -> None:
        """Save templates to JSON file.

        Persistence is routed through :class:`PersistedJSON`
        (``self._store``), which provides atomic write + single-slot
        ``.bak`` before overwrite + 0o600 perms (parity with
        ``config.py:1163-1182``). The shared ``_secure_atomic_write``
        applies ``O_NOFOLLOW`` on POSIX to prevent symlink TOCTOU
        attacks.

        M-62: previously this method caught *all* exceptions and
        silently logged them, returning ``None`` to callers. That
        meant a disk failure left the in-memory ``_templates`` list
        (already mutated by ``add``/``update``/``delete``) out of
        sync with what was actually on disk — the user's edit
        appeared to succeed (no error surfaced) but the next process
        restart would load the stale on-disk state and the edit
        would be lost. Now we log the error AND re-raise so callers
        can roll back their in-memory mutation and the IPC layer can
        surface the failure to the renderer.

        XZ-R11-06: caller is expected to hold ``self._lock`` (all
        current callers — the public CRUD methods — already do).
        """
        try:
            # PersistedJSON.save handles atomic write + .bak
            # + 0o600 perms + parent-dir creation in one call.
            # DJ-52: durability=False — the atomic os.replace still
            # guarantees consistency (no half-written files); only the
            # per-save fsync is dropped. Template edits are frequent
            # (CRUD ops from the settings UI) and a power-loss window
            # of a few seconds is acceptable.
            self._store.save({"templates": self._templates}, durability=False)
        except Exception:
            # M-62: log then re-raise so callers can roll back.
            # G4-H-38: use log.exception so the traceback is captured
            # automatically via sys.exc_info().
            log.exception("[TEMPLATES] Failed to save")
            raise
        log.debug("[TEMPLATES] Saved %d templates", len(self._templates))

    def add(self, trigger: str, output: str, *, match_mode: str = "exact") -> dict | None:
        """Add a new template. Returns the created template dict, or
        ``None`` if rejected.

        G4-M-38: enforces SEC-011-style caps:
          - Per-field length cap: ``MAX_TRIGGER_LENGTH``,
            ``MAX_OUTPUT_LENGTH``. Oversized entries are rejected with
            a logged warning.
          - Total count cap: ``MAX_TEMPLATES``. Once the cap is reached
            the new template is dropped (rejected with a warning).

        M-62: persists first-then-mutates with rollback. If ``_save``
        raises, the appended entry is popped back off so the
        in-memory state stays consistent with the on-disk state.

        XZ-R11-06: the entire read-validate-mutate-save-rebuild
        sequence runs under ``self._lock`` so a concurrent ``match``
        or CRUD call can't observe a half-applied mutation.
        """
        trigger_stripped = trigger.strip() if isinstance(trigger, str) else str(trigger).strip()
        output_str = output if isinstance(output, str) else str(output)
        with self._lock:
            if len(trigger_stripped) > MAX_TRIGGER_LENGTH:
                log.warning(
                    "[TEMPLATES] Trigger exceeds MAX_TRIGGER_LENGTH (%d > %d), rejecting",
                    len(trigger_stripped),
                    MAX_TRIGGER_LENGTH,
                )
                return None
            if len(output_str) > MAX_OUTPUT_LENGTH:
                log.warning(
                    "[TEMPLATES] Output exceeds MAX_OUTPUT_LENGTH (%d > %d), rejecting",
                    len(output_str),
                    MAX_OUTPUT_LENGTH,
                )
                return None
            if len(self._templates) >= MAX_TEMPLATES:
                log.warning(
                    "[TEMPLATES] Template count at MAX_TEMPLATES cap (%d), rejecting new template",
                    MAX_TEMPLATES,
                )
                return None
            template = {
                "trigger": trigger_stripped,
                "output": output_str,
                "match_mode": match_mode,  # "exact" or "contains"
                "created_at": datetime.now().isoformat(),
            }
            self._templates.append(template)
            try:
                self._save()
            except Exception:
                # Rollback: remove the template we just appended.
                # Use identity check (not equality) in case the template
                # dict happens to equal an earlier entry.
                for i in range(len(self._templates) - 1, -1, -1):
                    if self._templates[i] is template:
                        del self._templates[i]
                        break
                raise
            # S5-CR-60: rebuild match indexes after mutation.
            self._rebuild_indexes()
            return template

    def update(self, index: int, trigger: str, output: str, *, match_mode: str = "exact") -> dict | None:
        """Update a template by index. Returns the updated template or None.

        M-62: snapshots the original field values and restores them
        on save failure so the in-memory state stays consistent with
        the on-disk state.

        XZ-R11-06: the snapshot-mutate-save-rebuild sequence runs
        under ``self._lock`` so a concurrent ``match`` can't observe
        a half-updated entry.
        """
        with self._lock:
            if not (0 <= index < len(self._templates)):
                return None
            entry = self._templates[index]
            # Snapshot originals for rollback.
            old_trigger = entry.get("trigger")
            old_output = entry.get("output")
            old_match_mode = entry.get("match_mode")
            entry["trigger"] = trigger.strip()
            entry["output"] = output
            entry["match_mode"] = match_mode
            try:
                self._save()
            except Exception:
                entry["trigger"] = old_trigger
                entry["output"] = old_output
                entry["match_mode"] = old_match_mode
                raise
            # S5-CR-60: rebuild match indexes after mutation.
            self._rebuild_indexes()
            return entry

    def delete(self, index: int) -> bool:
        """Delete a template by index.

        M-62: snapshots the deleted entry and re-inserts it at the
        same index on save failure so the in-memory state stays
        consistent with the on-disk state.

        XZ-R11-06: the pop-save-rebuild sequence runs under
        ``self._lock`` so a concurrent ``match`` can't observe the
        list mid-pop.
        """
        with self._lock:
            if not (0 <= index < len(self._templates)):
                return False
            removed = self._templates.pop(index)
            try:
                self._save()
            except Exception:
                # Rollback: re-insert at the original index.
                self._templates.insert(index, removed)
                raise
            # S5-CR-60: rebuild match indexes after mutation.
            self._rebuild_indexes()
            return True

    # ── Import / Export ───────────────────────────────────────────────

    def export_json(self) -> str:
        """Export templates as a JSON string.

        XZ-R11-06: snapshot ``_templates`` under the lock before
        serializing so a concurrent CRUD mutation can't produce a
        half-serialized JSON (e.g. ``json.dumps`` observing a list
        mid-``pop``).
        """
        with self._lock:
            snapshot = list(self._templates)
        return json.dumps({"templates": snapshot}, indent=2, ensure_ascii=False)

    def import_json(self, json_str: str) -> int:
        """Import templates from a JSON string. Returns number imported.

        G4-M-38: enforces SEC-011-style caps:
          - Drops templates whose trigger exceeds
            ``MAX_TRIGGER_LENGTH`` or output exceeds
            ``MAX_OUTPUT_LENGTH`` (mirrors
            ``text_cleanup._load_external_corrections``).
          - Truncates the import if it would exceed ``MAX_TEMPLATES``.
          - Logs a single warning summarising the dropped count.

        M-62: snapshots the list before appending and restores it on
        save failure so the in-memory state stays consistent with the
        on-disk state.

        XZ-R11-06: the validate-extend-save-rebuild sequence runs
        under ``self._lock`` so a concurrent ``match`` can't observe
        a half-extended list.
        """
        with self._lock:
            try:
                data = json.loads(json_str)
                templates = data if isinstance(data, list) else data.get("templates", [])
                to_add: list[dict] = []
                dropped = 0
                for t in templates:
                    if not isinstance(t, dict) or "trigger" not in t or "output" not in t:
                        continue
                    trigger_raw = t.get("trigger", "")
                    output_raw = t.get("output", "")
                    trigger_str = trigger_raw if isinstance(trigger_raw, str) else str(trigger_raw)
                    output_str = output_raw if isinstance(output_raw, str) else str(output_raw)
                    # Use the stripped length for the trigger cap to match
                    # the add() behavior (which strips before storing).
                    if len(trigger_str.strip()) > MAX_TRIGGER_LENGTH:
                        dropped += 1
                        continue
                    if len(output_str) > MAX_OUTPUT_LENGTH:
                        dropped += 1
                        continue
                    to_add.append(t)
                if dropped:
                    log.warning(
                        "[TEMPLATES] Dropped %d templates from import (oversized)",
                        dropped,
                    )
                # Total-count cap: truncate to fit within MAX_TEMPLATES.
                current = len(self._templates)
                available = MAX_TEMPLATES - current
                if available <= 0:
                    log.warning(
                        "[TEMPLATES] Template count at MAX_TEMPLATES cap (%d), dropping all %d imported templates",
                        MAX_TEMPLATES,
                        len(to_add),
                    )
                    return 0
                if len(to_add) > available:
                    log.warning(
                        "[TEMPLATES] Import exceeds MAX_TEMPLATES cap, truncating %d -> %d",
                        len(to_add),
                        available,
                    )
                    to_add = to_add[:available]
                if not to_add:
                    return 0
                # Snapshot for rollback.
                old_len = len(self._templates)
                self._templates.extend(to_add)
                try:
                    self._save()
                except Exception:
                    # Rollback: truncate back to the pre-import length.
                    del self._templates[old_len:]
                    raise
                # S5-CR-60: rebuild match indexes after mutation.
                self._rebuild_indexes()
                return len(to_add)
            except Exception:
                log.exception("[TEMPLATES] Import failed")
                return 0

    # ── Matching ─────────────────────────────────────────────────────

    def match(self, text: str) -> str | None:
        """Try to match *text* against any template trigger.

        Returns the expanded output text (with variables substituted)
        if a match is found, or None if no template matches.

        Matching rules:
        - Whitespace-normalized, case-insensitive comparison
        - "exact" mode: the whole text must match the trigger
        - "contains" mode: the trigger must be found anywhere in the text
        - Shortest trigger wins when multiple templates match

        S5-CR-60: pre-fix this method did an O(N) linear scan of
        ``self._templates`` on every dictation. With MAX_TEMPLATES=1000
        that was up to 1000 iterations per call (re-normalizing each
        trigger's text on every call too). Now the exact-mode templates
        are in ``_exact_index`` (O(1) dict lookup) and the contains-mode
        templates are in ``_contains_list`` (sorted by trigger length
        ascending so we can early-exit once we see a trigger >= the
        current best length). The docstring's "shortest trigger wins"
        contract is preserved: the exact match (if any) sets the upper
        bound, and contains templates strictly shorter than that bound
        can still win — matching the pre-fix behavior where a short
        contains trigger beats a long exact trigger.

        XZ-R11-06: snapshot the match indexes under ``self._lock``
        BEFORE iterating so a concurrent CRUD mutation (which
        reassigns ``_exact_index`` / ``_contains_list`` via
        ``_rebuild_indexes``) can't leave ``match`` iterating a
        half-rebuilt list.  ``substitute_variables`` is called OUTSIDE
        the lock so the (potentially blocking) clipboard read in
        ``{clipboard}`` doesn't block concurrent CRUD calls.
        """
        with self._lock:
            if not text or not self._templates:
                return None

            normalized = _WHITESPACE_RE.sub(" ", text.strip()).lower()  # ER-55

            best_match: dict | None = None
            best_len = float("inf")

            # S5-CR-60: O(1) exact lookup. The exact match (if any) sets
            # the upper-bound length for the contains scan below.
            exact_t = self._exact_index.get(normalized)
            if exact_t is not None:
                best_match = exact_t
                best_len = len(normalized)

            # S5-CR-60: reduced-scan contains lookup. The list is sorted by
            # trigger length ascending; once we see a trigger whose length
            # is >= best_len, no subsequent (longer) trigger can beat the
            # current best, so we early-exit. Before any match is found
            # (best_len == inf) we scan the entire contains list.
            # XZ-R11-06: iterate a snapshot of the list so a concurrent
            # ``_rebuild_indexes`` (which reassigns ``_contains_list``)
            # can't truncate our iteration mid-scan.
            contains_snapshot = list(self._contains_list)
            for trigger_norm, t in contains_snapshot:
                if len(trigger_norm) >= best_len:
                    break
                if trigger_norm in normalized:
                    best_match = t
                    best_len = len(trigger_norm)

            if best_match is None:
                return None
            output = best_match["output"]

        # Substitute variables OUTSIDE the lock so the (potentially
        # blocking) clipboard read in ``{clipboard}`` doesn't block
        # concurrent CRUD calls.
        return substitute_variables(output)
