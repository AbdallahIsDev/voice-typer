"""mypy shadow stub for ``numpy.typing`` (see ``voice_typer/mypy_stubs/numpy/__init__.pyi``).

``numpy.typing``'s real stubs also use PEP 695 ``type`` statements and are
imported by third-party stub chains (e.g. scipy's ``.pyi`` files do
``import numpy.typing``). Same Any-``__getattr__`` pattern as the parent
shadow stub.
"""

from typing import Any

def __getattr__(name: str) -> Any: ...
