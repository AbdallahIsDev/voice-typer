"""mypy shadow stub for ``numpy.ma`` (see ``voice_typer/mypy_stubs/numpy/__init__.pyi``).

``numpy.ma``'s real stubs also use PEP 695 ``type`` statements and are
reached indirectly by mypy when following third-party stub chains (e.g.
scipy's ``.pyi`` files import ``numpy.ma``). Same Any-``__getattr__``
pattern as the parent shadow stub.
"""

from typing import Any

def __getattr__(name: str) -> Any: ...
