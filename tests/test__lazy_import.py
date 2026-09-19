"""Unit tests for ``voice_typer/server/_lazy_import.py``."""

from __future__ import annotations

import importlib
import sys
from unittest.mock import MagicMock

import pytest
from voice_typer.server._lazy_import import _LazyModule, lazy_module


@pytest.fixture
def fake_module(monkeypatch):
    """Install a MagicMock under a unique ``sys.modules`` key."""
    name = "_lazy_import_test_fake_module"
    mock = MagicMock(name=name)
    monkeypatch.setitem(sys.modules, name, mock)
    return name, mock


@pytest.fixture
def import_spy(monkeypatch):
    """Wrap ``importlib.import_module`` with a call-counting spy."""
    state: dict = {"calls": []}
    real_import = importlib.import_module

    def spy(name, *args, **kwargs):
        state["calls"].append(name)
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(importlib, "import_module", spy)
    return state


def test_lazy_module_returns_lazy_module_instance():
    """``lazy_module()`` must return a ``_LazyModule`` proxy instance."""
    proxy = lazy_module("os")
    assert isinstance(proxy, _LazyModule)


def test_lazy_module_repr_shows_module_name():
    """``__repr__`` exposes the wrapped module name for debugging."""
    proxy = lazy_module("some.deeply.nested.module")
    assert repr(proxy) == "<_LazyModule 'some.deeply.nested.module'>"


def test_lazy_module_does_not_import_on_construction(import_spy):
    """Constructing a proxy must NOT trigger import, that's the whole"""
    proxy = lazy_module("os")
    # Just constructing the proxy should leave import_module untouched.
    assert proxy is not None
    assert import_spy["calls"] == []


def test_getattr_triggers_import_on_first_access(fake_module, import_spy):
    """``sys.modules`` and returns the requested attribute."""
    name, mock = fake_module
    mock.some_attr = "hello"
    proxy = lazy_module(name)

    assert proxy.some_attr == "hello"
    assert import_spy["calls"] == [name]


def test_getattr_re_resolves_on_every_access_no_caching(fake_module, import_spy):
    """
    ``sys.modules``.
    This is the documented contract that makes per-test ``monkeypatch``
    """
    name, _ = fake_module
    proxy = lazy_module(name)

    _ = proxy.foo
    _ = proxy.foo
    _ = proxy.foo

    assert import_spy["calls"] == [name, name, name]


def test_getattr_reflects_sys_modules_changes_between_accesses(fake_module):
    """proxy must pick up the new module, proving it re-resolves each"""
    name, mock1 = fake_module
    mock1.value = 1

    proxy = lazy_module(name)
    assert proxy.value == 1

    # Swap the module out from under the proxy.  Use direct assignment
    mock2 = MagicMock()
    mock2.value = 2
    sys.modules[name] = mock2

    assert proxy.value == 2


def test_getattr_propagates_import_error(monkeypatch):
    """If ``import_module`` raises, the proxy must propagate, never"""

    def boom(name, *args, **kwargs):
        raise ModuleNotFoundError(f"No module named '{name}'")

    monkeypatch.setattr(importlib, "import_module", boom)
    proxy = lazy_module("nonexistent.module.xyz")

    with pytest.raises(ModuleNotFoundError):
        _ = proxy.anything


def test_getattr_propagates_attribute_error_from_wrapped_module(monkeypatch):
    """``AttributeError`` from the wrapped module must propagate so"""

    class RealModule:
        existing = "yes"

    name = "_lazy_import_test_attr_error_module"
    monkeypatch.setitem(sys.modules, name, RealModule())
    proxy = lazy_module(name)

    assert proxy.existing == "yes"
    with pytest.raises(AttributeError):
        _ = proxy.missing


def test_getattr_for_module_name_does_not_trigger_import(fake_module, import_spy):
    """import, slot lookup happens before ``__getattr__`` runs."""
    name, _ = fake_module
    proxy = lazy_module(name)

    # Should return the stored string, not call import_module.
    assert proxy._module_name == name
    assert import_spy["calls"] == []


def test_setattr_delegates_to_wrapped_module(fake_module):
    """``__setattr__`` writes to the wrapped module, not the proxy."""
    name, mock = fake_module
    proxy = lazy_module(name)

    proxy.new_value = 42

    assert mock.new_value == 42


def test_setattr_on_module_name_delegates_to_wrapped_not_slot(fake_module):
    """Setting ``proxy._module_name`` delegates to the wrapped module"""
    name, mock = fake_module
    proxy = lazy_module(name)

    proxy._module_name = "reassigned"

    # The wrapped module received the assignment.
    assert mock._module_name == "reassigned"
    # The proxy's actual slot is unchanged.
    assert object.__getattribute__(proxy, "_module_name") == name


def test_proxy_dict_delegates_to_wrapped_module(fake_module):
    """``__dict__`` is not a slot on ``_LazyModule``, so accessing it"""
    name, mock = fake_module
    mock.some_key = "some_value"
    proxy = lazy_module(name)

    assert proxy.__dict__ is mock.__dict__
    assert proxy.__dict__["some_key"] == "some_value"


def test_proxy_forwards_callable_attributes(fake_module):
    """transparent drop-in for ``import <name> as m; m.fn(...)``."""
    name, mock = fake_module
    mock.fn.return_value = "called"
    proxy = lazy_module(name)

    result = proxy.fn(1, 2, key="val")

    assert result == "called"
    mock.fn.assert_called_once_with(1, 2, key="val")


def test_delattr_delegates_to_wrapped_module(fake_module):
    """``__delattr__`` deletes the attribute on the wrapped module."""
    name, mock = fake_module
    mock.to_delete = "bye"
    proxy = lazy_module(name)

    del proxy.to_delete

    assert not hasattr(mock, "to_delete")


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
    """Two proxies for the same module name both resolve to the same"""
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


def test_monkeypatch_setattr_on_proxy_then_access_works(monkeypatch, fake_module):
    """Documented use case: ``monkeypatch.setattr(proxy, attr, value)``"""
    name, mock = fake_module
    proxy = lazy_module(name)

    monkeypatch.setattr(proxy, "InputStream", "fake_stream")

    assert proxy.InputStream == "fake_stream"
    assert mock.InputStream == "fake_stream"


# __setattr__ mutates sys.modules (load-bearing) ────────────────


def test_xv_78_setattr_mutates_real_sys_modules_entry(monkeypatch, fake_module):
    """XV-78 (LOAD-BEARING): ``__setattr__`` must mutate the real module"""
    name, mock = fake_module
    proxy = lazy_module(name)

    proxy.XV_78_sentinel = "load-bearing"

    # 1. The mock that's installed in sys.modules received the value.
    assert mock.XV_78_sentinel == "load-bearing"

    # 2. The object in sys.modules IS the mock (identity check, no
    assert sys.modules[name] is mock
    assert sys.modules[name].XV_78_sentinel == "load-bearing"

    # 3. A fresh ``importlib.import_module`` call picks up the mutation
    fresh = importlib.import_module(name)
    assert fresh is mock
    assert fresh.XV_78_sentinel == "load-bearing"


def test_xv_78_setattr_visible_to_independent_importer(monkeypatch, fake_module):
    """XV-78 follow-up: a mutation via the proxy is visible to ANY code"""
    name, mock = fake_module
    proxy = lazy_module(name)

    proxy.shared_state = "from-proxy"

    # An "independent importer", a fresh call that does NOT go through
    independent = importlib.import_module(name)
    assert independent is mock
    assert independent.shared_state == "from-proxy"

    assert proxy.shared_state == "from-proxy"


def test_xv_78_setattr_does_not_store_on_proxy_instance(fake_module):
    """the proxy instance itself. The proxy is stateless for attribute"""
    name, mock = fake_module
    proxy = lazy_module(name)

    # 1. The proxy class does NOT declare a ``__dict__`` slot, there
    assert "__dict__" not in _LazyModule.__slots__, (
        f"_LazyModule.__slots__ must not include '__dict__' (would allow "
        f"proxy-local attribute storage that bypasses the wrapped module); "
        f"got {_LazyModule.__slots__}"
    )

    # 2. The only slots are the two internal-only fields.
    assert set(_LazyModule.__slots__) == {"_module_name", "_cached_error"}, (
        f"_LazyModule.__slots__ must be exactly ('_module_name', '_cached_error'); got {_LazyModule.__slots__}"
    )

    # 3. After a setattr, both slot values are unchanged, the value
    proxy.should_not_land_on_proxy = True

    assert object.__getattribute__(proxy, "_module_name") == name
    assert object.__getattribute__(proxy, "_cached_error") is None

    # The value landed on the wrapped module, not on the proxy.
    assert mock.should_not_land_on_proxy is True


# ImportError caching ────────────────────────────────────────


class TestImportErrorCaching:
    """G4-M-43: ``ImportError`` is cached on the proxy so subsequent"""

    def test_import_error_cached_so_second_access_no_reimport(self, monkeypatch, import_spy):
        """A second attribute access after a failed import does NOT call"""
        call_count = {"n": 0}

        def boom(name, *args, **kwargs):
            call_count["n"] += 1
            raise ModuleNotFoundError(f"No module named '{name}'")

        monkeypatch.setattr(importlib, "import_module", boom)
        proxy = lazy_module("nonexistent.module.xyz")

        with pytest.raises(ModuleNotFoundError):
            _ = proxy.foo
        with pytest.raises(ModuleNotFoundError):
            _ = proxy.bar
        with pytest.raises(ModuleNotFoundError):
            _ = proxy.baz

        assert call_count["n"] == 1, f"import_module should be called once (cached), got {call_count['n']}"

    def test_cached_error_is_same_instance(self, monkeypatch):
        """The cached error is the SAME exception instance, re-raised"""
        original_error = ModuleNotFoundError("cached sentinel")

        def boom(name, *args, **kwargs):
            raise original_error

        monkeypatch.setattr(importlib, "import_module", boom)
        proxy = lazy_module("nonexistent.module.cached")

        with pytest.raises(ModuleNotFoundError) as exc_info_1:
            _ = proxy.foo
        with pytest.raises(ModuleNotFoundError) as exc_info_2:
            _ = proxy.bar

        assert exc_info_1.value is original_error
        assert exc_info_2.value is original_error
        # Same instance both times.
        assert exc_info_1.value is exc_info_2.value

    def test_cached_error_does_not_affect_sibling_proxy(self, monkeypatch):
        """the same module name, the cache is per-proxy, not per-module."""
        real_import = importlib.import_module

        # First proxy fails.
        def boom(name, *args, **kwargs):
            raise ModuleNotFoundError(f"No module named '{name}'")

        monkeypatch.setattr(importlib, "import_module", boom)
        proxy1 = lazy_module("nonexistent.module.sibling")
        with pytest.raises(ModuleNotFoundError):
            _ = proxy1.foo

        # Second proxy for the same name gets a fresh attempt, install
        mock = MagicMock()
        mock.value = "recovered"
        monkeypatch.setitem(sys.modules, "nonexistent.module.sibling", mock)
        monkeypatch.setattr(importlib, "import_module", real_import)
        proxy2 = lazy_module("nonexistent.module.sibling")
        assert proxy2.value == "recovered"

    def test_successful_import_does_not_cache_error(self, fake_module, import_spy):
        """A successful import leaves the cached-error slot ``None`` so"""
        name, mock = fake_module
        mock.value = "ok"
        proxy = lazy_module(name)

        # First access succeeds.
        assert proxy.value == "ok"
        # Cached error slot is still None.
        assert object.__getattribute__(proxy, "_cached_error") is None
        # Second access still re-resolves (no error cached to re-raise).
        assert proxy.value == "ok"
        assert import_spy["calls"] == [name, name]


class TestResetCache:
    """``reset_cache()`` clears the cached ``ImportError`` so the next"""

    def test_reset_cache_clears_cached_error(self, monkeypatch):
        """After ``reset_cache()``, the next access re-invokes"""
        call_count = {"n": 0}

        def boom(name, *args, **kwargs):
            call_count["n"] += 1
            raise ModuleNotFoundError(f"No module named '{name}'")

        monkeypatch.setattr(importlib, "import_module", boom)
        proxy = lazy_module("nonexistent.module.recover")

        # First access fails and caches the error.
        with pytest.raises(ModuleNotFoundError):
            _ = proxy.foo
        assert call_count["n"] == 1

        # Second access re-raises the cached error, import_module is
        with pytest.raises(ModuleNotFoundError):
            _ = proxy.bar
        assert call_count["n"] == 1

        proxy.reset_cache()
        assert object.__getattribute__(proxy, "_cached_error") is None

        # Third access re-attempts the import (and re-caches the new
        with pytest.raises(ModuleNotFoundError):
            _ = proxy.baz
        assert call_count["n"] == 2

    def test_reset_cache_recovers_when_module_becomes_available(self, monkeypatch):
        """If the underlying module becomes available after a failed"""
        real_import = importlib.import_module

        def boom(name, *args, **kwargs):
            raise ModuleNotFoundError(f"No module named '{name}'")

        monkeypatch.setattr(importlib, "import_module", boom)
        proxy = lazy_module("nonexistent.module.recover2")

        # First access fails, module is missing.
        with pytest.raises(ModuleNotFoundError):
            _ = proxy.foo

        # Make the module available via sys.modules + restore the real
        mock = MagicMock()
        mock.value = "recovered"
        monkeypatch.setitem(sys.modules, "nonexistent.module.recover2", mock)
        monkeypatch.setattr(importlib, "import_module", real_import)

        # Without reset_cache(), the cached error still re-raises.
        with pytest.raises(ModuleNotFoundError):
            _ = proxy.value

        # With reset_cache(), the next access succeeds.
        proxy.reset_cache()
        assert proxy.value == "recovered"

    def test_reset_cache_is_safe_when_no_error_cached(self, fake_module):
        """Calling ``reset_cache()`` on a healthy proxy (no cached error)"""
        name, mock = fake_module
        mock.value = "ok"
        proxy = lazy_module(name)

        # Successful access, no error cached.
        assert proxy.value == "ok"

        proxy.reset_cache()
        assert object.__getattribute__(proxy, "_cached_error") is None

        # Next access still resolves normally.
        assert proxy.value == "ok"


# ``_lazy_import.py`` (zero production callers, the promised startup
