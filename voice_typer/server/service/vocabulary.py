"""Vocabulary service mixin."""

import logging
import re

from voice_typer.server.service._base import ServiceMixinBase

log = logging.getLogger(__name__)


class VocabularyDuplicateError(Exception):
    """Raised when a vocabulary save would create a duplicate correction."""

    def __init__(self, phrase: str, count: int):
        super().__init__(f"duplicate correction: '{phrase}' ({count} entries)")
        self.phrase = phrase
        self.count = count


def _normalize_wrong_phrase(phrase: str) -> str:
    """Normalize a wrong phrase the way the matcher treats it."""
    return re.sub(r"\s+", " ", phrase.strip()).casefold()


def _flatten_category_entries(data: dict, cat: str) -> list[tuple[str, str]]:
    """Flatten one category's payload into (original, correction) pairs."""
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
    """Return the first duplicate ``(normalized_phrase, count)`` in *incoming*."""
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
    """Vocabulary-domain service methods."""

    def get_vocabulary(self) -> dict[str, object]:
        """Return the current vocabulary entries."""
        import copy

        # prefer the live VocabularyManager (already initialized on
        vm = getattr(self._app, "_vocabulary_manager", None)
        user_path_str: str | None = None
        if vm is not None and hasattr(vm, "get_all"):
            data = {k: copy.deepcopy(v) for k, v in vm.get_all().items()}
            if hasattr(vm, "_user_path"):
                user_path_str = str(vm._user_path)
        else:
            # Cold-start / test-fixture fallback: construct a
            from voice_typer.server.vocabulary import VocabularyManager

            fallback_vm = VocabularyManager(config_dir=self._app.config.config_dir)
            data = {k: copy.deepcopy(v) for k, v in fallback_vm.get_all().items()}
            user_path_str = str(fallback_vm._user_path) if hasattr(fallback_vm, "_user_path") else None
        # Attach the user-file path so the renderer can surface it in
        data["_user_file"] = user_path_str
        return data

    def save_vocabulary_with_diff(self, data: dict) -> dict[str, object]:
        """Save vocabulary with bundled diff logic."""
        from voice_typer.server.vocabulary import CATEGORIES, VocabularyManager

        # prefer the live VocabularyManager.
        live_vm = getattr(self._app, "_vocabulary_manager", None)
        if live_vm is not None and hasattr(live_vm, "_lock") and hasattr(live_vm, "_data"):
            with live_vm._lock:
                # CURRENT MERGED state (bundled + user file, tombstones
                merged = {cat: live_vm._data.get(cat) for cat in CATEGORIES}
                # RAW bundled defaults, the correct diff baseline. The
                if hasattr(live_vm, "_bundled_raw") and live_vm._bundled_raw:
                    bundled_defaults = live_vm._bundled_raw
                else:
                    # Fallback for managers built before the attribute
                    bundled_defaults = live_vm._load_bundled()
        else:
            # Cold-start / test-fixture fallback.
            mgr = VocabularyManager()
            _defaults = mgr._load_bundled()
            merged = {cat: _defaults.get(cat) for cat in CATEGORIES}
            bundled_defaults = _defaults

        # ``bundled`` (the merged state) is what the duplicate check
        bundled = merged

        # Backend-level duplicate enforcement, the single source of
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
            fallback_vm = VocabularyManager()
            fallback_vm._user_store.save(payload, durability=False)

        # reload the live VocabularyManager so its in-memory
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
        try:
            self._app.correction_usage.prune_entries(data or {})
        except Exception:
            log.debug(
                "[SERVICE] correction-usage prune after save failed",
                exc_info=True,
            )

        return {"imported_categories": len(user_only)}

    def get_correction_usage(self) -> dict[str, object]:
        """Return the per-correction usage snapshot."""
        return self._app.correction_usage.get_snapshot()

    def test_vocabulary_correction(self, text: str) -> dict[str, object]:
        """Apply the LIVE vocabulary rules to a phrase ("Test corrections" panel)."""
        vm = getattr(self._app, "_vocabulary_manager", None)
        if vm is None or not hasattr(vm, "apply_to_text"):
            from voice_typer.server.vocabulary import VocabularyManager

            vm = VocabularyManager(config_dir=self._app.config.config_dir)
        # track_usage=False: the "Test corrections" panel is a
        output = vm.apply_to_text(text, track_usage=False)
        return {"input": text, "output": output, "applied": output != text}
