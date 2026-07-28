"""Focused controller classes extracted from ``VoiceTyperApp``.

This package groups thin, single-responsibility controllers that own
specific side-effects previously inlined in
:class:`voice_typer.server.app.VoiceTyperApp`. Each controller takes a
reference to the owning app (``app``) and exposes a small surface
delegated to from the app's thin wrapper methods — behaviour is
preserved verbatim, only the class boundary moved.
"""
