"""``download_model`` return-shape contract test."""

from __future__ import annotations

from unittest.mock import MagicMock

from voice_typer.server.service import VoiceTyperService


def _make_service() -> VoiceTyperService:
    """Build a VoiceTyperService with a permissive mock app."""
    app = MagicMock()
    app.config.qwen_model_path = None
    app.config.huggingface_consent = True
    app.tray.notify = MagicMock()
    return VoiceTyperService(app)


def test_unknown_model_return_includes_model_field() -> None:
    """The dispatcher's unknown-model return path must include ``model``."""
    service = _make_service()
    result = service.download_model("nonexistent-model")
    assert result["success"] is False
    assert "Unknown model" in result["error"]
    assert result["model"] == "nonexistent-model", (
        "unknown-model return must include `model` so consumers don't have to thread the input through the IPC layer."
    )


def test_qwen_unconfigured_return_includes_model_field() -> None:
    """The Qwen-unconfigured return path must include ``model``."""
    service = _make_service()
    service._app.config.qwen_model_path = None
    result = service.download_model("qwen")
    assert result["success"] is False
    assert "Qwen model path not configured" in result["error"]
    assert result["model"] == "qwen", "qwen-unconfigured return must include `model`."


def test_generic_exception_return_includes_model_field(monkeypatch) -> None:
    """The dispatcher's outer ``except Exception`` handler must include"""
    service = _make_service()

    def _boom(*_args, **_kwargs):
        raise RuntimeError("synthetic registry failure")

    monkeypatch.setattr(
        "voice_typer.server.model_registry.get_model_metadata",
        _boom,
    )
    result = service.download_model("tiny.en")
    assert result["success"] is False
    assert "synthetic registry failure" in result["error"]
    assert result["model"] == "tiny.en", "generic-exception return must include `model`."


def test_all_download_model_return_paths_include_model() -> None:
    """``download_model`` dispatcher must contain a ``\"model\"`` key."""
    import ast
    import inspect
    import textwrap

    from voice_typer.server.service.model import ModelMixin

    for method_name in ("download_model", "_download_whisper_family", "_download_qwen", "_download_parakeet"):
        method = getattr(ModelMixin, method_name)
        src = textwrap.dedent(inspect.getsource(method))
        tree = ast.parse(src)
        func_def = tree.body[0]
        assert isinstance(func_def, ast.FunctionDef)

        # Collect every dict literal that appears in a Return statement.
        return_dicts: list[ast.Dict] = []
        for node in ast.walk(func_def):
            if isinstance(node, ast.Return) and isinstance(node.value, ast.Dict):
                return_dicts.append(node.value)

        assert return_dicts, f"{method_name} should have at least one ``return {{...}}`` literal to test."
        for d in return_dicts:
            keys = {k.value if isinstance(k, ast.Constant) else None for k in d.keys}
            assert "model" in keys, (
                f"{method_name} has a return dict literal without "
                f"a ``model`` key. Keys present: {sorted(k for k in keys if k)}. "
                "Every return path must populate `model` so the IPC layer "
                "and TS renderer can rely on the field."
            )
