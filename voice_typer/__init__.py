"""Voice Typer — background voice-to-text utility for Windows.

SEC-004: This module must not have side effects at import time.
Only ``__version__`` is exported; no heavy imports, no global state,
no file I/O, and no logging configuration happen at the module level.
"""

__version__ = "1.0.0"
