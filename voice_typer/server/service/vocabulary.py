"""Vocabulary domain mixin for VoiceTyperService.

Extracted verbatim from the original ``service.py`` god class
( split). Read / diff-save the user vocabulary file.
"""

import logging
import re

from voice_typer.server.service._base import ServiceMixinBase

log = logging.getLogger(__name__)


class VocabularyDuplicateError(Exception):
    """Raised when a vocabulary save would create a duplicate correction.

    ``save_vocabulary_with_diff`` raises this when the incoming data
    introduces a wrong phrase (normalized case-insensitively, the same
    way the dictation matcher keys lookups) that already exists in the
    current merged vocabulary. The IPC handler translates it into a
    ``client.duplicate_entry`` error envelope so the renderer can
    surface the localized "This correction already exists" message
    instead of a generic save failure.

    ``phrase`` is the normalized (casefolded, whitespace-collapsed)
    wrong phrase; ``count`` is the number of entries that would share
    it after the save.
    """

    def __init__(self, phrase: str, count: int):
        super().__init__(f"duplicate correction: '{phrase}' ({count} entries)")
        self.phrase = phrase
        self.count = count


def _normalize_wrong_phrase(phrase: str) -> str:
    """Normalize a wrong phrase the way the matcher treats it.

    Collapses internal whitespace runs to a single space and strips
    leading/trailing whitespace, then casefolds. This mirrors the
    case-insensitive lookup semantics of ``VocabularyManager.apply_to_text``
    (dict categories strip punctuation + lowercase; phrase categories
    compile with ``re.IGNORECASE``) so two entries the matcher would
    treat as the same wrong phrase are caught as duplicates.
    """
    return re.sub(r"\s+", " ", phrase.strip()).casefold()


def _flatten_category_entries(data: dict, cat: str) -> list[tuple[str, str]]:
    """Flatten one category's payload into (original, correction) pairs.

    Dict-based categories (misspellings, technical_terms, names,
    products) are ``{original: correction}``; list-based categories
    (phrase_corrections, extra_word_patterns) are ``[[wrong, correct], ...]``.
    Malformed entries are skipped (the same normalization the rest of
    the vocabulary layer applies).
    """
    entries: list[tuple[str, str]] = []
    raw = (data or {}).get(cat)
    if cat in ("misspellings", "technical_terms", "names", "products"):
        if isinstance(raw, dict):
            for k, v in raw.items():
                entries.append((k if isinstance(k, str) else str(k), v if isinstance(v, str) else str(v)))
    elif cat in ("phrase_corrections", "extra_word_patterns") and isinstance(raw, list):
        for item in raw:
            if isinstance(item, list | tuple) and len(item) >= 2:
                wrong = item[0] if isinstance(item[0], str) else str(item[0])
                good = item[1] if isinstance(item[1], str) else str(item[1])
                entries.append((wrong, good))
    return entries


def _find_new_duplicate(
    incoming: dict,
    baseline: dict,
) -> tuple[str, int] | None:
    """Return the first duplicate ``(normalized_phrase, count)`` in *incoming*.

    A phrase is a duplicate when it appears in ≥2 incoming entries AND
    at least one of those occurrences is NOT already present in the
    baseline (the current merged vocabulary, bundled + user file).

    Baseline occurrences are matched by exact ``(original, correction)``
    and consumed one-for-one, so a plain echo of pre-existing duplicate
    entries (e.g. the renderer sending the full merged list back on an
    unrelated edit) is allowed — the save only rejects when it would
    CREATE a new duplicate. Returns ``None`` when the save introduces
    no new duplicates.
    """
    baseline_counts: dict[str, list[tuple[str, str]]] = {}
    for cat in ("misspellings", "technical_terms", "names", "products", "phrase_corrections", "extra_word_patterns"):
        for entry in _flatten_category_entries(baseline, cat):
            baseline_counts.setdefault(_normalize_wrong_phrase(entry[0]), []).append(entry)

    incoming_counts: dict[str, list[tuple[str, str]]] = {}
    for cat in ("misspellings", "technical_terms", "names", "products", "phrase_corrections", "extra_word_patterns"):
        for entry in _flatten_category_entries(incoming, cat):
            incoming_counts.setdefault(_normalize_wrong_phrase(entry[0]), []).append(entry)

    for phrase, occs in incoming_counts.items():
        if len(occs) < 2:
            continue
        # every incoming occurrence must be matched by a baseline
        # occurrence of the same normalized phrase (one-for-one, exact
        # original+correction) for the duplicate to be pre-existing.
        unmatched = len(occs)
        remaining = list(baseline_counts.get(phrase, []))
        for occ in occs:
            for i, base_occ in enumerate(remaining):
                if base_occ == occ:
                    remaining.pop(i)
                    unmatched -= 1
                    break
        if unmatched > 0:
            return phrase, len(occs)
    return None


class VocabularyMixin(ServiceMixinBase):
    """Vocabulary-domain service methods.

    Delegates to :class:`voice_typer.server.vocabulary.VocabularyManager`
    and persists only user customizations (diff against bundled
    defaults) so duplicate entries can't accumulate on reload.
    """

    # Vocabulary () ───────────────────────────────────

    def get_vocabulary(self) -> dict[str, object]:
        """Return the current vocabulary entries.

        (fix): previously called ``vm.list_entries()`` which
                does not exist on VocabularyManager, causing a 500 error on
                every Vocabulary page load. The renderer's ``VocabularyData``
                type expects a dict keyed by category name (misspellings,
                technical_terms, names, products, phrase_corrections,
                extra_word_patterns) — same shape as ``VocabularyManager.get_all()``.
                We now delegate to ``get_all()`` and add the user-file path so
                the renderer can show "edited" indicators.

        reuse the live VocabularyManager (already initialized
                on the app via the ``_vocabulary_manager`` lazy property) instead
                of constructing a throwaway per IPC call. The old impl did a
                full disk read (bundled corrections.json + user vocabulary.json)
                + merge on EVERY Vocabulary page load — a double file read on
                every IPC call. We now reuse the already-merged ``_data`` on
                the live instance. A fresh-instance fallback is kept for test
                fixtures / cold-start paths where ``_vocabulary_manager`` is
                None.

        return a DEEP copy of the live data so the renderer
                can't mutate the in-memory ``_data`` dict via the returned
                reference. ``VocabularyManager.get_all()`` returns a SHALLOW
                ``dict(self._data)`` copy — the top-level dict is unique but
                the per-category values (dicts / lists) are the SAME objects
                the manager iterates in ``apply_to_text``. A renderer that
                mutated a returned category (e.g. ``data['misspellings'].pop(...)``)
                would corrupt the live vocabulary mid-dictation. We deep-copy
                each category value before returning.
        """
        import copy

        # prefer the live VocabularyManager (already initialized on
        # the app via the lazy ``_vocabulary_manager`` property).
        vm = getattr(self._app, "_vocabulary_manager", None)
        user_path_str: str | None = None
        if vm is not None and hasattr(vm, "get_all"):
            data = {k: copy.deepcopy(v) for k, v in vm.get_all().items()}
            if hasattr(vm, "_user_path"):
                user_path_str = str(vm._user_path)
        else:
            # Cold-start / test-fixture fallback: construct a
            # throwaway VocabularyManager so the Vocabulary page can
            # still render (with bundled defaults) even if the live
            # instance is not yet initialized.
            from voice_typer.server.vocabulary import VocabularyManager

            fallback_vm = VocabularyManager(config_dir=self._app.config.config_dir)
            data = {k: copy.deepcopy(v) for k, v in fallback_vm.get_all().items()}
            user_path_str = str(fallback_vm._user_path) if hasattr(fallback_vm, "_user_path") else None
        # Attach the user-file path so the renderer can surface it in
        # the UI (e.g. "edit the file directly at ...").
        data["_user_file"] = user_path_str
        return data

    def save_vocabulary_with_diff(self, data: dict) -> dict[str, object]:
        """Save vocabulary with bundled diff logic.

        Moved from ipc_server.py.  Only saves user customizations
                (diff against bundled defaults) to the user file, preventing
                duplicate entries on next load.

        after writing the user file, reload the live
                ``self._app._vocabulary_manager`` so its in-memory ``_data``
                reflects the just-written user file. dictation_pipeline uses
                ``_vocabulary_manager.apply_to_text()`` on the live instance;
                without this reload it would use stale state until app restart.

        reuse the live VocabularyManager (already initialized
                on the app) instead of constructing a throwaway per IPC call.
                The old impl loaded bundled + user from disk and built a merged
                ``_data`` only to discard it - a double file read on every IPC
                call. We now read the bundled defaults from the live manager's
                already-merged ``_data`` under its existing lock. A fresh-instance
                fallback is kept for test fixtures / cold-start paths where
                ``_vocabulary_manager`` is None.
        """
        from voice_typer.server.vocabulary import CATEGORIES, VocabularyManager

        # prefer the live VocabularyManager.
        live_vm = getattr(self._app, "_vocabulary_manager", None)
        if live_vm is not None and hasattr(live_vm, "_lock") and hasattr(live_vm, "_data"):
            with live_vm._lock:
                # CURRENT MERGED state (bundled + user file, tombstones
                # already applied by ``_load_and_merge``) — the baseline
                # for the duplicate check and for computing deletions.
                merged = {cat: live_vm._data.get(cat) for cat in CATEGORIES}
                # RAW bundled defaults — the correct diff baseline. The
                # user file stores ONLY customizations relative to these
                # defaults; diffing against the MERGED state instead
                # would drop unchanged user entries on every subsequent
                # save (the wholesale user-file replace would lose them).
                if hasattr(live_vm, "_bundled_raw") and live_vm._bundled_raw:
                    bundled_defaults = live_vm._bundled_raw
                else:
                    # Fallback for managers built before the attribute
                    # existed (or cold instances that skipped
                    # ``_load_and_merge``).
                    bundled_defaults = live_vm._load_bundled()
        else:
            # Cold-start / test-fixture fallback.
            mgr = VocabularyManager()
            _defaults = mgr._load_bundled()
            merged = {cat: _defaults.get(cat) for cat in CATEGORIES}
            bundled_defaults = _defaults

        # ``bundled`` (the merged state) is what the duplicate check
        # compares against — an entry already in the file is not a NEW
        # duplicate.
        bundled = merged

        # Backend-level duplicate enforcement — the single source of
        # truth for "does this pair already exist". The renderer sends
        # the FULL merged list on every save (quick-add, edit dialog,
        # import, delete, clear), so the authoritative check must live
        # in this write path, not in any individual UI component.
        # ``bundled`` here is the CURRENT merged state (bundled defaults
        # + user file, pre-save); ``data`` is the incoming payload.
        # ``_find_new_duplicate`` allows a plain echo of pre-existing
        # duplicates (e.g. the bundled ``to 2`` pair in legacy data) but
        # rejects any save that would CREATE a new duplicate wrong
        # phrase (case-insensitive) — the matcher treats two entries
        # with the same normalized wrong phrase as the same lookup, so
        # a second one silently overwrites or double-fires.
        duplicate = _find_new_duplicate(data or {}, bundled)
        if duplicate is not None:
            raise VocabularyDuplicateError(duplicate[0], duplicate[1])

        user_only: dict[str, object] = {}
        for cat in CATEGORIES:
            incoming = (data or {}).get(cat)
            default_cat = bundled_defaults.get(cat)

            if cat in ("misspellings", "technical_terms", "names", "products"):
                if isinstance(incoming, dict):
                    bd = default_cat if isinstance(default_cat, dict) else {}
                    diff = {k: v for k, v in incoming.items() if bd.get(k) != v}
                    if diff:
                        user_only[cat] = diff
            elif cat in ("phrase_corrections", "extra_word_patterns") and isinstance(incoming, list):
                bs: set[tuple[str, str]] = set()
                if isinstance(default_cat, list):
                    for item in default_cat:
                        if isinstance(item, list | tuple) and len(item) >= 2:
                            bs.add((item[0], item[1]))
                diff = [
                    item
                    for item in incoming
                    if isinstance(item, list | tuple) and len(item) >= 2 and (item[0], item[1]) not in bs
                ]
                if diff:
                    user_only[cat] = diff

        # Deletion tombstones. The diff alone can only express ADDITIONS /
        # VALUE CHANGES relative to the bundled defaults — removing a
        # BUNDLED default entry leaves no trace in the diff, so the entry
        # resurrects on the next merge. Persist a reserved ``_deleted``
        # map (category → list of removed keys / pairs) next to the user
        # diff; ``VocabularyManager._load_and_merge`` applies it. Entries
        # present in the CURRENT merged state but absent from the incoming
        # payload are deletions; previously-tombstoned entries that
        # reappear in the payload are re-additions (tombstone cleared).
        prev_deleted: dict[str, list] = {}
        if live_vm is not None and hasattr(live_vm, "_user_store"):
            try:
                _prev_raw = live_vm._user_store.load()
                if isinstance(_prev_raw, dict) and isinstance(_prev_raw.get("_deleted"), dict):
                    prev_deleted = _prev_raw["_deleted"]
            except Exception:
                # Best-effort: a failed read must not block the save.
                prev_deleted = {}

        deleted: dict[str, list] = {}
        for cat in CATEGORIES:
            incoming = (data or {}).get(cat)
            merged_cat = merged.get(cat)
            prev_list = prev_deleted.get(cat)
            prev_set: set = (
                {(tuple(p) if isinstance(p, (list, tuple)) else p) for p in prev_list}
                if isinstance(prev_list, list)
                else set()
            )
            removed: set = set()
            if cat in ("misspellings", "technical_terms", "names", "products"):
                if isinstance(merged_cat, dict):
                    incoming_keys = set(incoming) if isinstance(incoming, dict) else set()
                    removed = set(merged_cat.keys()) - incoming_keys
                    # Keep prior tombstones unless the entry was re-added.
                    removed |= {k for k in prev_set if not isinstance(k, tuple) and k not in incoming_keys}
            else:
                if isinstance(merged_cat, list):
                    incoming_pairs: set = (
                        {tuple(p) for p in incoming if isinstance(p, (list, tuple)) and len(p) >= 2}
                        if isinstance(incoming, list)
                        else set()
                    )
                    merged_pairs: set = {tuple(p) for p in merged_cat if isinstance(p, (list, tuple)) and len(p) >= 2}
                    removed = merged_pairs - incoming_pairs
                    # Keep prior tombstones unless the pair was re-added.
                    removed |= {p for p in prev_set if isinstance(p, tuple) and p not in incoming_pairs}
            if removed:
                deleted[cat] = sorted(removed, key=str)

        # Write only user customizations to the user file.
        #
        # Route through the live VocabularyManager's ``_user_store``
        # (a :class:`PersistedJSON` instance) instead of calling
        # ``_secure_atomic_write`` directly.  This gives the user
        # vocabulary the same single-slot ``.bak`` before overwrite +
        # corrupt-quarantine + recovery guarantees that
        # ``VocabularyManager._save_user`` already relies on — closing
        # the gap where this IPC path silently bypassed them (a crash
        # mid-write would leave a half-written user file with no .bak
        # to recover from).  ``durability=False`` matches
        # ``_save_user``'s choice: vocabulary edits are frequent and a
        # power-loss window of a few seconds is acceptable; the atomic
        # ``os.replace`` still guarantees no half-written files.
        #
        # Diff-recompute semantics: we save ONLY ``user_only`` (the diff
        # against bundled), NOT ``live_vm._data`` (the full merged
        # data).  Saving the full merged data would break the
        # load-time merge (list-based categories would double on every
        # save → reload cycle).  ``_user_store.save(user_only)`` writes
        # the user-only dict directly through PersistedJSON's atomic
        # path, which is exactly what we want.
        # The user file carries the diff PLUS the tombstone map under the
        # reserved ``_deleted`` key (omitted entirely when empty so clean
        # files stay clean).
        payload: dict[str, object] = dict(user_only)
        if deleted:
            payload["_deleted"] = deleted

        if live_vm is not None and hasattr(live_vm, "_user_store"):
            try:
                with live_vm._lock:
                    live_vm._user_store.save(payload, durability=False)
            except Exception:
                log.error(
                    "[SERVICE] save_vocabulary_with_diff: PersistedJSON.save via live VocabularyManager failed",
                    exc_info=True,
                )
                raise
        else:
            # Cold-start / test-fixture fallback: build a throwaway
            # VocabularyManager just to get its ``_user_store``
            # (PersistedJSON with .bak + quarantine + 0o600 perms).
            # This is slower than the live-vm path (an extra
            # bundled+user load), but cold-start is rare and the
            # safety parity is worth it.
            fallback_vm = VocabularyManager()
            fallback_vm._user_store.save(payload, durability=False)

        # reload the live VocabularyManager so its in-memory
        # ``_data`` reflects the just-written user file.
        if live_vm is not None and hasattr(live_vm, "_lock") and hasattr(live_vm, "_load_and_merge"):
            try:
                with live_vm._lock:
                    live_vm._load_and_merge()
            except Exception:
                log.debug(
                    "[SERVICE] save_vocabulary_with_diff: live VocabularyManager reload failed",
                    exc_info=True,
                )

        # Drop usage records for corrections that no longer exist —
        # ``data`` is the FULL merged payload the renderer sent, so
        # deleting a correction also removes its counter (the usage
        # file can't grow with dead entries). Best-effort: a failure
        # here must not fail the vocabulary save.
        try:
            self._app.correction_usage.prune_entries(data or {})
        except Exception:
            log.debug(
                "[SERVICE] correction-usage prune after save failed",
                exc_info=True,
            )

        return {"imported_categories": len(user_only)}

    def get_correction_usage(self) -> dict[str, object]:
        """Return the per-correction usage snapshot.

        Feeds the Vocabulary page's "used Nx / last triggered" per
        entry and the Analytics page's corrections-applied rate
        (corrections ÷ dictations, from the ``*_by_day`` maps).
        """
        return self._app.correction_usage.get_snapshot()

    def test_vocabulary_correction(self, text: str) -> dict[str, object]:
        """Apply the LIVE vocabulary rules to a phrase ("Test corrections" panel).

        Runs the actual ``VocabularyManager.apply_to_text`` pass — the
        same engine dictation uses — so the preview can never drift
        from production behavior. Falls back to a throwaway manager
        when the live ``_vocabulary_manager`` is not yet initialized
        (cold start / test fixtures).
        """
        vm = getattr(self._app, "_vocabulary_manager", None)
        if vm is None or not hasattr(vm, "apply_to_text"):
            from voice_typer.server.vocabulary import VocabularyManager

            vm = VocabularyManager(config_dir=self._app.config.config_dir)
        # track_usage=False: the "Test corrections" panel is a
        # PREVIEW — firing it must not inflate the real usage numbers
        # that the Vocabulary/Analytics pages report.
        output = vm.apply_to_text(text, track_usage=False)
        return {"input": text, "output": output, "applied": output != text}
