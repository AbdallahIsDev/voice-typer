"""Vocabulary domain mixin for VoiceTyperService.

Extracted verbatim from the original ``service.py`` god class
( split). Read / diff-save the user vocabulary file.
"""

import logging

from voice_typer.server.service._base import ServiceMixinBase

log = logging.getLogger(__name__)


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
                bundled = {cat: live_vm._data.get(cat) for cat in CATEGORIES}
        else:
            # Cold-start / test-fixture fallback.
            mgr = VocabularyManager()
            bundled = mgr._load_bundled()

        user_only: dict[str, object] = {}
        for cat in CATEGORIES:
            incoming = (data or {}).get(cat)
            bundled_cat = bundled.get(cat)

            if cat in ("misspellings", "technical_terms", "names", "products"):
                if isinstance(incoming, dict):
                    bd = bundled_cat if isinstance(bundled_cat, dict) else {}
                    diff = {k: v for k, v in incoming.items() if bd.get(k) != v}
                    if diff:
                        user_only[cat] = diff
            elif cat in ("phrase_corrections", "extra_word_patterns") and isinstance(incoming, list):
                bs: set[tuple[str, str]] = set()
                if isinstance(bundled_cat, list):
                    for item in bundled_cat:
                        if isinstance(item, list | tuple) and len(item) >= 2:
                            bs.add((item[0], item[1]))
                diff = [
                    item
                    for item in incoming
                    if isinstance(item, list | tuple) and len(item) >= 2 and (item[0], item[1]) not in bs
                ]
                if diff:
                    user_only[cat] = diff

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
        if live_vm is not None and hasattr(live_vm, "_user_store"):
            try:
                with live_vm._lock:
                    live_vm._user_store.save(user_only, durability=False)
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
            fallback_vm._user_store.save(user_only, durability=False)

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

        return {"imported_categories": len(user_only)}
