"""Vocabulary domain mixin for VoiceTyperService.

Extracted verbatim from the original ``service.py`` god class
(ARCH-005 split). Read / diff-save the user vocabulary file.
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

    # ── Vocabulary (ARCH-005) ───────────────────────────────────

    def get_vocabulary(self) -> dict[str, object]:
        """Return the current vocabulary entries.

        ERR-IPC-005 (fix): previously called ``vm.list_entries()`` which
        does not exist on VocabularyManager, causing a 500 error on
        every Vocabulary page load. The renderer's ``VocabularyData``
        type expects a dict keyed by category name (misspellings,
        technical_terms, names, products, phrase_corrections,
        extra_word_patterns) — same shape as ``VocabularyManager.get_all()``.
        We now delegate to ``get_all()`` and add the user-file path so
        the renderer can show "edited" indicators.
        """
        from voice_typer.server.vocabulary import VocabularyManager

        vm = VocabularyManager(config_dir=self._app.config.config_dir)
        data = vm.get_all()
        # Attach the user-file path so the renderer can surface it in
        # the UI (e.g. "edit the file directly at ...").
        data["_user_file"] = str(vm._user_path) if hasattr(vm, "_user_path") else None
        return data

    def save_vocabulary_with_diff(self, data: dict) -> dict[str, object]:
        """Save vocabulary with bundled diff logic.

        ARCH-005: Moved from ipc_server.py.  Only saves user customizations
        (diff against bundled defaults) to the user file, preventing
        duplicate entries on next load.

        XZ-R11-01: after writing the user file, reload the live
        ``self._app._vocabulary_manager`` so its in-memory ``_data``
        reflects the just-written user file. dictation_pipeline uses
        ``_vocabulary_manager.apply_to_text()`` on the live instance;
        without this reload it would use stale state until app restart.

        XZ-R11-10: reuse the live VocabularyManager (already initialized
        on the app) instead of constructing a throwaway per IPC call.
        The old impl loaded bundled + user from disk and built a merged
        ``_data`` only to discard it - a double file read on every IPC
        call. We now read the bundled defaults from the live manager's
        already-merged ``_data`` under its existing lock. A fresh-instance
        fallback is kept for test fixtures / cold-start paths where
        ``_vocabulary_manager`` is None.
        """
        import json

        from voice_typer.server.config import _config_dir
        from voice_typer.server.vocabulary import CATEGORIES, VOCAB_FILENAME, VocabularyManager

        # XZ-R11-10: prefer the live VocabularyManager.
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

        # Write only user customizations to the user file
        # SEC-003: Use _secure_atomic_write to ensure 0o600 permissions
        user_path = _config_dir() / VOCAB_FILENAME
        user_path.parent.mkdir(parents=True, exist_ok=True)
        from voice_typer.server.config import _secure_atomic_write

        _secure_atomic_write(
            user_path,
            json.dumps(user_only, indent=2, ensure_ascii=False),
        )

        # XZ-R11-01: reload the live VocabularyManager so its in-memory
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
