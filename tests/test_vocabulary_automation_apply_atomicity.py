"""Atomicity regression tests for ``VocabularyAutomation`` apply / dismiss."""

from __future__ import annotations

import ast
import inspect
import json
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

VOCAB_AUTO_SOURCE = Path(
    inspect.getsourcefile(__import__("voice_typer.server.vocabulary_automation", fromlist=["x"]))
).read_text()


@pytest.fixture
def vocab_dir(tmp_config_dir):
    return tmp_config_dir


@pytest.fixture
def bundled(tmp_path):
    data = {
        "misspellings": {},
        "phrase_corrections": [],
        "extra_word_patterns": [],
        "technical_terms": {},
        "names": {},
        "products": {},
    }
    path = tmp_path / "corrections.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


@pytest.fixture
def vm(vocab_dir, bundled):
    from voice_typer.server.vocabulary import VocabularyManager

    return VocabularyManager(config_dir=vocab_dir, bundled_path=bundled)


@pytest.fixture
def config():
    return SimpleNamespace(
        vocabulary_automation_enabled=True,
        vocabulary_auto_confidence_threshold=0.7,
        vocabulary_auto_apply_threshold=0.95,
    )


@pytest.fixture
def automation(vm, config):
    from voice_typer.server.vocabulary_automation import VocabularyAutomation

    return VocabularyAutomation(vm, config)


def _make_suggestion(original="definately", corrected="definitely", confidence=0.97):
    from voice_typer.server.vocabulary_automation import CorrectionSuggestion

    return CorrectionSuggestion(
        original=original,
        corrected=corrected,
        confidence=confidence,
        context=original,
        timestamp=0.0,
    )


def _func_node(source: str, name: str) -> ast.FunctionDef | None:
    """Return the AST FunctionDef for ``name`` in ``source`` (or None)."""
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    return None


def _first_with(body: list[ast.stmt]) -> ast.With | None:
    """Return the first ``with`` statement in ``body`` (or None)."""
    for stmt in body:
        if isinstance(stmt, ast.With):
            return stmt
    return None


def _body_after_docstring(body: list[ast.stmt]) -> list[ast.stmt]:
    """a function body so the atomicity assertions check the actual code,"""
    if (
        body
        and isinstance(body[0], ast.Expr)
        and isinstance(body[0].value, ast.Constant)
        and isinstance(body[0].value.value, str)
    ):
        return body[1:]
    return body


class TestSourceLevelAtomicity:
    """The lock-acquisition pattern is statically verifiable, runtime"""

    def test_apply_suggestion_holds_lock_for_whole_body(self):
        node = _func_node(VOCAB_AUTO_SOURCE, "apply_suggestion")
        assert node is not None, "apply_suggestion must exist"
        # The body (after the docstring) must be a single `with self._lock:`
        body = _body_after_docstring(node.body)
        assert len(body) == 1, (
            f"apply_suggestion body must be a single `with self._lock:` "
            f"statement (got {len(body)} top-level stmts), anything "
            f"outside the lock re-opens the check-and-apply race"
        )
        with_stmt = body[0]
        assert isinstance(with_stmt, ast.With), "apply_suggestion body must be a `with` statement"
        # Verify the context manager is self._lock.
        ctx = with_stmt.items[0].context_expr
        assert isinstance(ctx, ast.Attribute), "with context must be an attribute access"
        assert ctx.attr == "_lock", "with context must be self._lock"

    def test_dismiss_suggestion_holds_lock_for_whole_body(self):
        node = _func_node(VOCAB_AUTO_SOURCE, "dismiss_suggestion")
        assert node is not None
        body = _body_after_docstring(node.body)
        assert len(body) == 1, (
            f"dismiss_suggestion body must be a single `with self._lock:` statement (got {len(body)} top-level stmts)"
        )
        with_stmt = body[0]
        assert isinstance(with_stmt, ast.With)
        ctx = with_stmt.items[0].context_expr
        assert isinstance(ctx, ast.Attribute)
        assert ctx.attr == "_lock"

    def test_auto_apply_holds_lock_across_iteration(self):
        """lock for the ENTIRE iteration loop, not just for the snapshot."""
        node = _func_node(VOCAB_AUTO_SOURCE, "auto_apply_high_confidence_suggestions")
        assert node is not None
        body = _body_after_docstring(node.body)
        with_stmts = [s for s in body if isinstance(s, ast.With)]
        assert len(with_stmts) >= 1, "auto_apply_high_confidence_suggestions must wrap the loop in `with self._lock:`"
        with_stmt = with_stmts[0]
        ctx = with_stmt.items[0].context_expr
        assert isinstance(ctx, ast.Attribute)
        assert ctx.attr == "_lock", "with context must be self._lock"
        # The for loop must be INSIDE the with block.
        for_loops = [s for s in with_stmt.body if isinstance(s, ast.For)]
        assert len(for_loops) >= 1, (
            "the for loop must be inside the `with self._lock:` block, "
            "if it's outside, the snapshot-and-iterate race is back"
        )

    def test_auto_apply_calls_inner_locked_helper_not_public_apply(self):
        """Inside the lock, ``auto_apply_high_confidence_suggestions``"""
        node = _func_node(VOCAB_AUTO_SOURCE, "auto_apply_high_confidence_suggestions")
        assert node is not None
        calls = [n for n in ast.walk(node) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)]
        called_methods = {c.func.attr for c in calls}
        assert "_apply_suggestion_locked" in called_methods, (
            "auto_apply must call _apply_suggestion_locked (lock-held helper) "
            f"inside the with-block, found calls: {called_methods}"
        )
        assert "apply_suggestion" not in called_methods, (
            "auto_apply must NOT call the public apply_suggestion (would "
            "re-acquire the lock / reintroduce the check-then-apply race "
            "between iterations)"
        )


class _CountingVM:
    """Wraps a real VocabularyManager and counts ``add_entry`` calls."""

    def __init__(self, real_vm):
        self._real = real_vm
        self.add_entry_count = 0
        self.add_entry_args: list[tuple] = []
        self._count_lock = threading.Lock()

    def add_entry(self, category, key, value):
        with self._count_lock:
            self.add_entry_count += 1
            self.add_entry_args.append((category, key, value))
        return self._real.add_entry(category, key, value)

    def __getattr__(self, name):
        return getattr(self._real, name)


class TestAutoApplyConcurrency:
    def test_concurrent_auto_apply_no_duplicates(self, vm, config):
        """N threads call ``auto_apply_high_confidence_suggestions``"""
        from voice_typer.server.vocabulary_automation import VocabularyAutomation

        counting_vm = _CountingVM(vm)
        automation = VocabularyAutomation(counting_vm, config)

        # 5 distinct high-confidence suggestions, all qualifying.
        suggestions = [
            _make_suggestion(original=f"word{i}", corrected=f"correct{i}", confidence=0.99) for i in range(5)
        ]
        automation._pending.extend(suggestions)

        n_threads = 12
        barrier = threading.Barrier(n_threads)

        def worker():
            barrier.wait()
            automation.auto_apply_high_confidence_suggestions(threshold=0.95)

        threads = [threading.Thread(target=worker) for _ in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5.0)

        # Each of the 5 suggestions must have been applied exactly once.
        assert counting_vm.add_entry_count == 5, (
            f"expected 5 add_entry calls (one per suggestion), got "
            f"{counting_vm.add_entry_count}, duplicate applies detected"
        )
        # All suggestions must be marked applied.
        for s in suggestions:
            assert s.applied, f"suggestion {s.original!r} not marked applied"
        # No suggestion should be in the pending list anymore.
        assert automation.get_pending_suggestions() == []


class TestApplySuggestionConcurrency:
    def test_concurrent_apply_same_suggestion_no_duplicates(self, vm, config):
        """N threads call ``apply_suggestion`` on the SAME suggestion"""
        from voice_typer.server.vocabulary_automation import VocabularyAutomation

        counting_vm = _CountingVM(vm)
        automation = VocabularyAutomation(counting_vm, config)
        suggestion = _make_suggestion(original="definately", corrected="definitely")
        automation._pending.append(suggestion)

        n_threads = 12
        barrier = threading.Barrier(n_threads)

        def worker():
            barrier.wait()
            automation.apply_suggestion(suggestion)

        threads = [threading.Thread(target=worker) for _ in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5.0)

        assert counting_vm.add_entry_count == 1, (
            f"expected exactly 1 add_entry call, got {counting_vm.add_entry_count}, duplicate apply detected"
        )
        assert suggestion.applied


class TestMixedApplyAutoApplyRace:
    def test_auto_apply_and_manual_apply_no_duplicates(self, vm, config):
        """``auto_apply_high_confidence_suggestions`` racing on the same"""
        from voice_typer.server.vocabulary_automation import VocabularyAutomation

        counting_vm = _CountingVM(vm)
        automation = VocabularyAutomation(counting_vm, config)
        suggestion = _make_suggestion(original="teh", corrected="the", confidence=0.99)
        automation._pending.append(suggestion)

        n_threads = 8
        barrier = threading.Barrier(n_threads * 2)

        def manual_worker():
            barrier.wait()
            automation.apply_suggestion(suggestion)

        def auto_worker():
            barrier.wait()
            automation.auto_apply_high_confidence_suggestions(threshold=0.95)

        threads = [threading.Thread(target=manual_worker) for _ in range(n_threads)]
        threads += [threading.Thread(target=auto_worker) for _ in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5.0)

        assert counting_vm.add_entry_count == 1, (
            f"expected exactly 1 add_entry call (manual + auto race), got "
            f"{counting_vm.add_entry_count}, duplicate apply detected"
        )
        assert suggestion.applied


class TestIdempotency:
    def test_apply_twice_calls_add_entry_once(self, automation, vm):
        suggestion = _make_suggestion(original="teh", corrected="the")
        automation._pending.append(suggestion)

        automation.apply_suggestion(suggestion)
        automation.apply_suggestion(suggestion)  # second call: no-op

        miss = vm.get_category("misspellings")
        assert miss.get("teh") == "the"
        assert suggestion.applied
        assert suggestion not in automation.get_pending_suggestions()

    def test_apply_after_dismiss_is_noop(self, automation, vm):
        suggestion = _make_suggestion(original="teh", corrected="the")
        automation._pending.append(suggestion)

        automation.dismiss_suggestion(suggestion)
        automation.apply_suggestion(suggestion)  # must be a no-op

        miss = vm.get_category("misspellings")
        # No entry should have been added (dismiss wins).
        assert "teh" not in miss or miss.get("teh") != "the"
        assert suggestion.dismissed
        # ``applied`` must NOT be set (dismiss won).
        assert not suggestion.applied

    def test_dismiss_after_apply_is_noop(self, automation, vm):
        suggestion = _make_suggestion(original="teh", corrected="the")
        automation._pending.append(suggestion)

        automation.apply_suggestion(suggestion)
        automation.dismiss_suggestion(suggestion)  # must be a no-op

        # The apply already ran; the entry should be present.
        miss = vm.get_category("misspellings")
        assert miss.get("teh") == "the"
        assert suggestion.applied
        # ``dismissed`` must NOT be flipped (apply already won).
        assert not suggestion.dismissed


class TestApplyLockedReturnValue:
    def test_returns_true_on_new_apply(self, automation):
        suggestion = _make_suggestion()
        automation._pending.append(suggestion)
        with automation._lock:
            assert automation._apply_suggestion_locked(suggestion) is True

    def test_returns_false_on_already_applied(self, automation):
        suggestion = _make_suggestion()
        suggestion.applied = True
        with automation._lock:
            assert automation._apply_suggestion_locked(suggestion) is False

    def test_returns_false_on_already_dismissed(self, automation):
        suggestion = _make_suggestion()
        suggestion.dismissed = True
        with automation._lock:
            assert automation._apply_suggestion_locked(suggestion) is False

    def test_returns_false_when_add_entry_raises(self, automation):
        suggestion = _make_suggestion()

        def boom(*args, **kwargs):
            raise RuntimeError("simulated persistence failure")

        automation._vm.add_entry = boom  # type: ignore[method-assign]
        with automation._lock:
            assert automation._apply_suggestion_locked(suggestion) is False
        # The applied flag must NOT be set when add_entry failed.
        assert not suggestion.applied
