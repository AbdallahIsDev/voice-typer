"""Unit tests for ``voice_typer/server/_lazy_import.py``.

PERF-COLDSTART-001 introduced ``_LazyModule`` as a stateless proxy that
defers ``import <name>`` to first attribute access.  These tests pin the
public contract of the module:

- ``lazy_module()`` returns a ``_LazyModule`` and never imports on
  construction.
- ``__getattr__`` triggers a real ``importlib.import_module`` call on
  every access (no caching — the proxy is stateless so per-test
  ``monkeypatch.setitem(sys.modules, ...)`` mocks are always honoured).
- ``__getattr__`` propagates ``ImportError`` / ``AttributeError`` — it
  never silently returns ``None``.
- ``__setattr__`` and ``__delattr__`` delegate to the wrapped module, so
  ``monkeypatch.setattr(proxy, attr, value)`` keeps working.
- Accessing the private ``_module_name`` slot does NOT trigger an
  import (slot lookup happens before ``__getattr__``).
- Multiple proxies are independent; two proxies for the same module
  share state through ``sys.modules``.

All tests use ``monkeypatch`` to install lightweight ``MagicMock``
fakes in ``sys.modules`` (or to spy on ``importlib.import_module``) —
no real audio, GUI, or network deps are loaded.
"""

from __future__ import annotations

import importlib
import sys
from unittest.mock import MagicMock

import pytest
from voice_typer.server._lazy_import import _LazyModule, lazy_module

# ── Fixtures ───────────────────────────────────────────────────────────


@pytest.fixture
def fake_module(monkeypatch):
    """Install a MagicMock under a unique ``sys.modules`` key.

    Returns ``(name, mock)`` so individual tests can mutate the mock
    (or replace it) between accesses to verify the proxy re-resolves
    on every attribute lookup.
    """
    name = "_lazy_import_test_fake_module"
    mock = MagicMock(name=name)
    monkeypatch.setitem(sys.modules, name, mock)
    return name, mock


@pytest.fixture
def import_spy(monkeypatch):
    """Wrap ``importlib.import_module`` with a call-counting spy.

    Returns a dict ``{"calls": list[str], "real": Callable}`` so tests
    can assert on which module names were resolved and how many times.
    The real ``import_module`` is still invoked (so ``sys.modules``
    lookups behave normally), it is just observed.
    """
    state: dict = {"calls": []}
    real_import = importlib.import_module

    def spy(name, *args, **kwargs):
        state["calls"].append(name)
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(importlib, "import_module", spy)
    return state


# ── lazy_module() factory ─────────────────────────────────────────────


def test_lazy_module_returns_lazy_module_instance():
    """``lazy_module()`` must return a ``_LazyModule`` proxy instance."""
    proxy = lazy_module("os")
    assert isinstance(proxy, _LazyModule)


def test_lazy_module_repr_shows_module_name():
    """``__repr__`` exposes the wrapped module name for debugging."""
    proxy = lazy_module("some.deeply.nested.module")
    assert repr(proxy) == "<_LazyModule 'some.deeply.nested.module'>"


def test_lazy_module_does_not_import_on_construction(import_spy):
    """Constructing a proxy must NOT trigger import — that's the whole
    point of PERF-COLDSTART-001 (defer heavy imports to first use).
    """
    proxy = lazy_module("os")
    # Just constructing the proxy should leave import_module untouched.
    assert proxy is not None
    assert import_spy["calls"] == []


# ── __getattr__ ───────────────────────────────────────────────────────


def test_getattr_triggers_import_on_first_access(fake_module, import_spy):
    """First attribute access resolves the wrapped module from
    ``sys.modules`` and returns the requested attribute.
    """
    name, mock = fake_module
    mock.some_attr = "hello"
    proxy = lazy_module(name)

    assert proxy.some_attr == "hello"
    assert import_spy["calls"] == [name]


def test_getattr_re_resolves_on_every_access_no_caching(fake_module, import_spy):
    """The proxy is stateless: every access re-resolves from
    ``sys.modules``.

    This is the documented contract that makes per-test ``monkeypatch``
    mocks safe (no stale caching, no cross-test leakage).  Note: this
    differs from a typical lazy-import cache that imports once — the
    design here explicitly re-resolves every time.
    """
    name, _ = fake_module
    proxy = lazy_module(name)

    proxy.foo
    proxy.foo
    proxy.foo

    assert import_spy["calls"] == [name, name, name]


def test_getattr_reflects_sys_modules_changes_between_accesses(fake_module):
    """If the entry in ``sys.modules`` is swapped between accesses, the
    proxy must pick up the new module — proving it re-resolves each
    time rather than caching the first resolved module.
    """
    name, mock1 = fake_module
    mock1.value = 1

    proxy = lazy_module(name)
    assert proxy.value == 1

    # Swap the module out from under the proxy.  Use direct assignment
    # rather than monkeypatch — the fixture's teardown will still
    # restore the pre-fixture state.
    mock2 = MagicMock()
    mock2.value = 2
    sys.modules[name] = mock2

    assert proxy.value == 2


def test_getattr_propagates_import_error(monkeypatch):
    """If ``import_module`` raises, the proxy must propagate — never
    silently return ``None`` or a MagicMock.
    """

    def boom(name, *args, **kwargs):
        raise ModuleNotFoundError(f"No module named '{name}'")

    monkeypatch.setattr(importlib, "import_module", boom)
    proxy = lazy_module("nonexistent.module.xyz")

    with pytest.raises(ModuleNotFoundError):
        proxy.anything


def test_getattr_propagates_attribute_error_from_wrapped_module(monkeypatch):
    """``AttributeError`` from the wrapped module must propagate so
    ``hasattr(proxy, attr)`` returns ``False`` (rather than triggering
    a spurious import or returning a MagicMock).
    """

    class RealModule:
        existing = "yes"

    name = "_lazy_import_test_attr_error_module"
    monkeypatch.setitem(sys.modules, name, RealModule())
    proxy = lazy_module(name)

    assert proxy.existing == "yes"
    with pytest.raises(AttributeError):
        proxy.missing


def test_getattr_for_module_name_does_not_trigger_import(fake_module, import_spy):
    """Accessing the private ``_module_name`` slot must NOT trigger an
    import — slot lookup happens before ``__getattr__`` runs.
    """
    name, _ = fake_module
    proxy = lazy_module(name)

    # Should return the stored string, not call import_module.
    assert proxy._module_name == name
    assert import_spy["calls"] == []


# ── __setattr__ ───────────────────────────────────────────────────────


def test_setattr_delegates_to_wrapped_module(fake_module):
    """``__setattr__`` writes to the wrapped module, not the proxy.

    This is what makes the documented use case work::

        monkeypatch.setattr(recording.sd, "InputStream", fake)
    """
    name, mock = fake_module
    proxy = lazy_module(name)

    proxy.new_value = 42

    assert mock.new_value == 42


def test_setattr_on_module_name_delegates_to_wrapped_not_slot(fake_module):
    """Setting ``proxy._module_name`` delegates to the wrapped module
    (because ``__setattr__`` always delegates).  It does NOT reassign
    the proxy's own slot — ``object.__setattr__`` is only used in
    ``__init__``.
    """
    name, mock = fake_module
    proxy = lazy_module(name)

    proxy._module_name = "reassigned"

    # The wrapped module received the assignment.
    assert mock._module_name == "reassigned"
    # The proxy's actual slot is unchanged.
    assert object.__getattribute__(proxy, "_module_name") == name


def test_proxy_dict_delegates_to_wrapped_module(fake_module):
    """``__dict__`` is not a slot on ``_LazyModule``, so accessing it
    falls through to ``__getattr__`` and returns the wrapped module's
    ``__dict__`` — the proxy itself has no instance dict of its own.

    This is a useful transparency guarantee: code that inspects
    ``proxy.__dict__`` sees the wrapped module's namespace, not the
    proxy's internals.
    """
    name, mock = fake_module
    mock.some_key = "some_value"
    proxy = lazy_module(name)

    # proxy.__dict__ resolves to mock.__dict__ via __getattr__.
    assert proxy.__dict__ is mock.__dict__
    assert proxy.__dict__["some_key"] == "some_value"


def test_proxy_forwards_callable_attributes(fake_module):
    """Calling a function returned by ``proxy.fn(...)`` must invoke the
    wrapped module's ``fn`` with the same arguments — the proxy is a
    transparent drop-in for ``import <name> as m; m.fn(...)``.
    """
    name, mock = fake_module
    mock.fn.return_value = "called"
    proxy = lazy_module(name)

    result = proxy.fn(1, 2, key="val")

    assert result == "called"
    mock.fn.assert_called_once_with(1, 2, key="val")


# ── __delattr__ ───────────────────────────────────────────────────────


def test_delattr_delegates_to_wrapped_module(fake_module):
    """``__delattr__`` deletes the attribute on the wrapped module."""
    name, mock = fake_module
    mock.to_delete = "bye"
    proxy = lazy_module(name)

    del proxy.to_delete

    assert not hasattr(mock, "to_delete")


# ── Cross-proxy independence & sharing ────────────────────────────────


def test_multiple_proxies_for_different_modules_are_independent(monkeypatch):
    """Two proxies for different module names must not interfere."""
    mock_a = MagicMock()
    mock_b = MagicMock()
    mock_a.value = "a"
    mock_b.value = "b"
    monkeypatch.setitem(sys.modules, "_lazy_import_test_mod_a", mock_a)
    monkeypatch.setitem(sys.modules, "_lazy_import_test_mod_b", mock_b)

    proxy_a = lazy_module("_lazy_import_test_mod_a")
    proxy_b = lazy_module("_lazy_import_test_mod_b")

    assert proxy_a.value == "a"
    assert proxy_b.value == "b"


def test_two_proxies_for_same_module_share_state(fake_module):
    """Two proxies for the same module name both resolve to the same
    ``sys.modules`` entry, so mutations via one are visible from the
    other.
    """
    name, mock = fake_module
    mock.shared = "x"

    p1 = lazy_module(name)
    p2 = lazy_module(name)

    assert p1.shared == "x"
    assert p2.shared == "x"

    # Mutating via p1 is visible via p2.
    p1.shared = "y"
    assert p2.shared == "y"
    assert mock.shared == "y"


# ── Drop-in compatibility ────────────────────────────────────────────


def test_monkeypatch_setattr_on_proxy_then_access_works(monkeypatch, fake_module):
    """Documented use case: ``monkeypatch.setattr(proxy, attr, value)``
    followed by ``proxy.attr`` works because both go through the wrapped
    module — call sites in recording.py / tray_icon.py don't need to
    change when switching from a real ``import`` to ``lazy_module()``.
    """
    name, mock = fake_module
    proxy = lazy_module(name)

    monkeypatch.setattr(proxy, "InputStream", "fake_stream")

    assert proxy.InputStream == "fake_stream"
    assert mock.InputStream == "fake_stream"
