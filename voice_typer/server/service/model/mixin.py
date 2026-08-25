"""Assembled ModelMixin - public facade class of the package."""

from __future__ import annotations

from ._delete_import import DeleteImportMixin
from ._download_state import DownloadStateMixin
from ._downloads import DownloadsMixin
from ._status import StatusMixin


class ModelMixin(
    DownloadStateMixin,
    StatusMixin,
    DeleteImportMixin,
    DownloadsMixin,
):
    """Model-domain service methods.

    Covers download/delete/import/status, per-download cancellation
    ( / SERVICE-1), dependency probes, and the HuggingFace
    consent gate ( / ).
    """
