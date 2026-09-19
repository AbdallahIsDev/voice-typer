"""Structural tests for the ``download_model`` dispatcher split."""

from __future__ import annotations

import ast
import inspect
import textwrap

from voice_typer.server.service._download_helpers import (
    DownloadOutcome,
    notify,
    poll_download_progress,
    push_progress,
)
from voice_typer.server.service.model import ModelMixin


def _dedent_method_source(method) -> str:
    """Return the source of ``method`` dedented so ``ast.parse`` accepts it."""
    src = inspect.getsource(method)
    return textwrap.dedent(src)


def test_download_model_delegates_to_three_branch_methods() -> None:
    """name. A revert to inline whisper/qwen/parakeet logic would remove"""
    src = inspect.getsource(ModelMixin.download_model)
    assert "_download_whisper_family" in src, (
        "download_model must delegate to _download_whisper_family for "
        "whisper / distil-whisper variants. A revert to inline logic "
        "would re-create the 558-LOC god method."
    )
    assert "_download_qwen" in src, (
        "download_model must delegate to _download_qwen for the Qwen "
        "backend. A revert to inline logic would re-create the 558-LOC "
        "god method."
    )
    assert "_download_parakeet" in src, (
        "download_model must delegate to _download_parakeet for the "
        "Parakeet backend. A revert to inline logic would re-create "
        "the 558-LOC god method."
    )


def test_branch_methods_exist_on_modelmixin() -> None:
    """All three branch methods are defined on :class:`ModelMixin`."""
    assert callable(getattr(ModelMixin, "_download_whisper_family", None)), (
        "_download_whisper_family must be a method on ModelMixin."
    )
    assert callable(getattr(ModelMixin, "_download_qwen", None)), "_download_qwen must be a method on ModelMixin."
    assert callable(getattr(ModelMixin, "_download_parakeet", None)), (
        "_download_parakeet must be a method on ModelMixin."
    )


def test_branch_methods_return_download_outcome() -> None:
    """Each branch method is annotated to return :data:`DownloadOutcome`"""
    for branch_name in (
        "_download_whisper_family",
        "_download_qwen",
        "_download_parakeet",
    ):
        branch = getattr(ModelMixin, branch_name)
        hints = inspect.get_annotations(branch, eval_str=False)
        ret = hints.get("return")
        # The return annotation is the string "DownloadOutcome" (because
        ret_str = ret if isinstance(ret, str) else getattr(ret, "__name__", str(ret))
        assert "DownloadOutcome" in ret_str, (
            f"{branch_name} must be annotated as ``-> DownloadOutcome``. Got return annotation: {ret!r}."
        )


def test_download_model_is_compact_dispatcher() -> None:
    """docstring). We assert the function body (excluding docstring) has"""
    src = _dedent_method_source(ModelMixin.download_model)
    tree = ast.parse(src)
    func_def = tree.body[0]
    assert isinstance(func_def, ast.FunctionDef)
    body = func_def.body
    # Drop the docstring (first statement if it's a bare string expr).
    if (
        body
        and isinstance(body[0], ast.Expr)
        and isinstance(body[0].value, ast.Constant)
        and isinstance(body[0].value.value, str)
    ):
        body = body[1:]
    statement_count = len(body)
    assert statement_count <= 40, (
        f"download_model has {statement_count} statements (excluding "
        "docstring), must remain a compact dispatcher (≤ 40 "
        "statements). A revert to the 558-LOC god method would have "
        "hundreds of statements."
    )


def test_download_model_does_not_inline_polling_loop() -> None:
    """``download_model`` must NOT contain an inline ``while`` polling"""
    src = _dedent_method_source(ModelMixin.download_model)
    tree = ast.parse(src)
    func_def = tree.body[0]
    assert isinstance(func_def, ast.FunctionDef)
    # Walk the function body looking for ``while`` statements.
    while_count = sum(1 for node in ast.walk(func_def) if isinstance(node, ast.While))
    assert while_count == 0, (
        f"download_model must not contain a ``while`` loop, the "
        f"polling loop was extracted to poll_download_progress. Found "
        f"{while_count} ``while`` statements in the dispatcher body."
    )


def test_download_model_does_not_define_nested_closures() -> None:
    """``download_model`` must NOT define nested function closures —"""
    src = _dedent_method_source(ModelMixin.download_model)
    tree = ast.parse(src)
    func_def = tree.body[0]
    assert isinstance(func_def, ast.FunctionDef)
    nested_func_count = sum(1 for node in ast.walk(func_def) if isinstance(node, ast.FunctionDef))
    # The outer FunctionDef itself is counted by ast.walk, so subtract 1.
    nested_func_count -= 1
    assert nested_func_count == 0, (
        f"download_model must not define nested function closures, "
        f"the _push_progress and _notify closures were lifted to "
        f"module-level helpers. Found {nested_func_count} nested "
        f"FunctionDefs in the dispatcher body."
    )


def test_module_level_helpers_exist() -> None:
    """The three helpers lifted from the original ``download_model``"""
    assert callable(push_progress), (
        "push_progress must be a module-level function in "
        "_download_helpers.py (extracted from the _push_progress "
        "closure that lived inside download_model)."
    )
    assert callable(notify), (
        "notify must be a module-level function in "
        "_download_helpers.py (extracted from the _notify closure that "
        "lived inside download_model)."
    )
    assert callable(poll_download_progress), (
        "poll_download_progress must be a module-level function in "
        "_download_helpers.py (extracted from the polling loop that "
        "lived inside download_model)."
    )


def test_download_outcome_typeddict_exists() -> None:
    """The :data:`DownloadOutcome` TypedDict is the uniform return type"""
    # ``DownloadOutcome`` is a TypedDict subclass.
    from typing import _TypedDictMeta  # type: ignore[attr-defined]

    assert isinstance(DownloadOutcome, _TypedDictMeta), (
        f"DownloadOutcome must be a TypedDict, got {type(DownloadOutcome)!r}."
    )
    # Verify the documented fields exist.
    expected_fields = {
        "success",
        "error",
        "model",
        "message",
        "cancelled",
        "consent_required",
        "reason",
    }
    actual_fields = set(DownloadOutcome.__annotations__.keys())
    missing = expected_fields - actual_fields
    assert not missing, (
        f"DownloadOutcome TypedDict is missing fields: {sorted(missing)}. "
        "These fields mirror the 10 distinct return shapes the original "
        "monolithic download_model produced."
    )


def test_helpers_take_explicit_args_not_self() -> None:
    """The lifted helpers must be PURE module-level functions, they"""
    push_sig = inspect.signature(push_progress)
    push_params = list(push_sig.parameters.keys())
    assert push_params[0] == "event_bus", (
        f"push_progress must take ``event_bus`` as its first explicit arg (not ``self``). Got params: {push_params}."
    )
    assert "model_name" in push_params, "push_progress must take model_name."
    assert "progress" in push_params, "push_progress must take progress."
    assert "status" in push_params, "push_progress must take status."

    notify_sig = inspect.signature(notify)
    notify_params = list(notify_sig.parameters.keys())
    assert notify_params[0] == "tray", (
        f"notify must take ``tray`` as its first explicit arg (not ``self``). Got params: {notify_params}."
    )

    poll_sig = inspect.signature(poll_download_progress)
    poll_params = set(poll_sig.parameters.keys())
    expected_poll_params = {
        "thread",
        "target_bytes",
        "target_mb",
        "model_name",
        "repo_id",
        "cache_dir",
        "download_id",
        "event_bus",
        "is_cancelled_fn",
    }
    missing = expected_poll_params - poll_params
    assert not missing, (
        f"poll_download_progress is missing expected kwargs: {sorted(missing)}. Got params: {sorted(poll_params)}."
    )


def test_require_huggingface_consent_returns_download_outcome_or_none() -> None:
    """``_require_huggingface_consent`` is annotated to return"""
    hints = inspect.get_annotations(ModelMixin._require_huggingface_consent, eval_str=False)
    ret = hints.get("return")
    ret_str = ret if isinstance(ret, str) else getattr(ret, "__name__", str(ret))
    assert "DownloadOutcome" in ret_str, (
        f"_require_huggingface_consent must be annotated as ``-> DownloadOutcome | None``. Got: {ret!r}."
    )
    assert "None" in ret_str, (
        "_require_huggingface_consent must allow ``None`` return (consent given, caller proceeds)."
    )


def test_no_type_ignore_return_value_in_model_py() -> None:
    """The 2 ``# type: ignore[return-value]`` comments that previously"""
    import io
    import tokenize
    from pathlib import Path

    import voice_typer.server.service.model as model_mod

    # The mixin was split into a package, scan every leaf so the ban
    pkg_dir = Path(model_mod.__file__).resolve().parent
    bad_ignores: list[str] = []
    for leaf in sorted(pkg_dir.glob("*.py")):
        src = leaf.read_text(encoding="utf-8")
        tokens = tokenize.tokenize(io.BytesIO(src.encode("utf-8")).readline)
        for tok in tokens:
            if tok.type != tokenize.COMMENT:
                continue
            comment_text = tok.string
            if "type: ignore" in comment_text and "return-value" in comment_text:
                bad_ignores.append(f"{leaf.name}:{tok.start[0]}: {comment_text}")
    assert not bad_ignores, (
        "Found ``# type: ignore[return-value]`` comments in the service/model package "
        f"({len(bad_ignores)} occurrence(s)). These hide real shape mismatches "
        "between the consent-gate dict and the DownloadOutcome TypedDict. "
        "Fix: change _require_huggingface_consent's return type to "
        "DownloadOutcome | None. Occurrences: " + "; ".join(bad_ignores)
    )
