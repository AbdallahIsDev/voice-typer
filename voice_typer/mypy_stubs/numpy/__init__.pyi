"""mypy shadow stub for numpy (PEP 561 Any-module).

The real numpy 2.x stubs ship ``.pyi`` files that use PEP 695 ``type``
statements (Python 3.12-only syntax), which mypy cannot even PARSE at the
project's mypy language level (``[tool.mypy] python_version = "3.10"``,
CQ-058). A parse error cannot be suppressed via ``ignore_errors`` or
``follow_imports`` (verified empirically on mypy 1.x and 2.x — both parse
numpy's ``__init__.pyi`` regardless of the follow mode).

This stub package shadows the real numpy for mypy only (via ``[tool.mypy]
mypy_path = ["voice_typer/mypy_stubs"]``): every attribute resolves to
``Any`` through PEP 562 ``__getattr__`` — the same effective behaviour as
when the real stubs fail to parse, minus the fatal syntax error. Runtime
behaviour is unaffected (stubs are never imported at runtime). pyrefly is
untouched — it keeps its own search path (``voice_typer/stubs``) and
resolves the real numpy through the active interpreter's site-packages at
its 3.12 language level.

Must be a PACKAGE (``numpy/__init__.pyi``), not a module file
(``numpy.pyi``): mypy resolves submodules (``numpy.ma``, ``numpy.typing``)
through the package directory, and a module-file stub silently loses
submodule resolution to the real site-packages package.
"""

from typing import Any

def __getattr__(name: str) -> Any: ...
