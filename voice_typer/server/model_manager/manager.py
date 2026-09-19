"""Assembled ModelManager - public facade class of the package."""

from __future__ import annotations

from ._base import ModelManagerCore
from ._change import ChangeMixin
from ._construction import ConstructionMixin
from ._lifecycle import LifecycleMixin
from ._loading import LoadingMixin
from ._notify import LastResortNotifyMixin


class ModelManager(
    LoadingMixin,
    ChangeMixin,
    LastResortNotifyMixin,
    ConstructionMixin,
    LifecycleMixin,
    ModelManagerCore,
):
    """PERF-015: includes an LRU cache for loaded models. When loading a"""
