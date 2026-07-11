# PYREFLY-001: stub for the `Cocoa` umbrella framework (pyobjc-
# framework-Cocoa, macOS only). Cocoa re-exports Foundation + AppKit +
# CoreData; we mirror that so any `from Cocoa import X` resolves.
from typing import Any

# Re-export AppKit symbols.
from AppKit import *  # noqa: F401,F403

# Re-export Foundation symbols.
from Foundation import *  # noqa: F401,F403

# Re-export CoreData (minimal).
NSManagedObject: Any
NSManagedObjectContext: Any
NSPersistentContainer: Any

__all__: list[str]
