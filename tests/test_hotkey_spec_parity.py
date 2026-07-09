"""RW-1: cross-parser parity tests for the unified hotkey parser.

The backend previously had four independent hotkey parsers that
diverged on modifier-alias handling, key-name normalisation, and
multi-key handling. RW-1 (Hotkey parser unification) introduced
``voice_typer/server/hotkey_spec.py`` as the SINGLE CANONICAL parser
and updated the four legacy parsers to delegate to it:

1. ``_parse_hotkey_parts`` in ``config_validators.py`` — returns
   ``list[str]`` of canonical tokens.
2. ``_parse_hotkey_to_pynput`` in ``hotkeys.py`` — returns pynput
   ``Key`` / ``KeyCode`` objects (or a ``(modifiers, target)`` tuple).
3. ``parse_hotkey_to_win32`` in ``hotkeys.py`` — returns
   ``(vk, modifiers)`` for Win32 ``RegisterHotKey``.
4. ``parse_hotkey_spec`` in ``native_hotkeys.py`` — returns a dict
   with ``modifiers``, ``main_key``, ``is_modifier_only``, etc.

These tests run all four parsers over a corpus of hotkey strings and
verify that, after applying each adapter's documented
platform-specific collapses, they agree on:

- whether the spec is empty / unparseable,
- the set of canonical modifier names,
- the main non-modifier key (normalised to the wire-protocol name).

Documented platform-specific collapses (these are NOT parser bugs —
they reflect real platform limitations):

- ``win`` / ``super`` / ``cmd`` collapse to ``"cmd"`` in the pynput
  adapter (pynput has only ``Key.cmd``) and the native_hotkeys
  adapter (the wire protocol emits ``Cmd`` / ``Win`` / ``Super``
  interchangeably per platform, so they must compare equal for
  cross-platform matching).
- ``win`` / ``super`` / ``cmd`` collapse to ``_MOD_WIN`` (a single
  bit) in the Win32 adapter (``RegisterHotKey`` does not distinguish).
- ``alt_gr`` collapses to ``"altgr"`` (no underscore) in the
  native_hotkeys adapter, and to ``_MOD_ALTGR`` in the Win32 adapter.

The canonical parser itself PRESERVES the distinction (``win``,
``super``, ``cmd``, ``alt_gr`` are four different canonical names).
The parity test normalises all adapter outputs to the SAME collapsed
form before comparing.
"""

from __future__ import annotations

from typing import Optional

import pytest

from voice_typer.server.hotkey_spec import (
    CANONICAL_MODIFIERS,
    MODIFIER_ALIASES,
    HotkeySpec,
    parse_hotkey,
)


# ─── Common collapsed form ───────────────────────────────────────────────


def _collapse_modifier(name: str) -> str:
    """Normalise a canonical modifier name to the COMMON COLLAPSED FORM.

    The canonical parser preserves ``win`` / ``super`` / ``cmd`` as
    three distinct names, but every adapter that talks to a real
    platform API collapses them. To compare adapter outputs against
    the canonical parser, we collapse the canonical form the same way.

    The collapsed form is:

    - ``win`` / ``super`` → ``"cmd"`` (matches pynput + native_hotkeys)
    - ``alt_gr`` → ``"altgr"`` (matches native_hotkeys wire name)
    - everything else: unchanged
    """
    if name in ("win", "super"):
        return "cmd"
    if name == "alt_gr":
        return "altgr"
    return name


def _collapse_modifiers(modifiers: frozenset[str] | set[str] | tuple[str, ...]) -> frozenset[str]:
    """Apply :func:`_collapse_modifier` to a collection of modifiers."""
    return frozenset(_collapse_modifier(m) for m in modifiers)


# ─── Wire-protocol key normalisation ─────────────────────────────────────


def _to_wire_name(canonical_key: str) -> Optional[str]:
    """Convert a canonical (lowercase) key name to the wire-protocol name.

    Delegates to ``native_hotkeys._normalize_key_name`` so the parity
    test uses the SAME mapping the native_hotkeys adapter uses. This
    makes the comparison meaningful: every adapter's main-key output
    is normalised to the wire-protocol name.
    """
    from voice_typer.server.native_hotkeys import _normalize_key_name

    if not canonical_key:
        return None
    return _normalize_key_name(canonical_key)


# ─── Per-adapter canonicalisers ──────────────────────────────────────────
#
# Each canonicaliser takes an adapter's raw output (or None) and returns
# a tuple ``(modifiers_set, main_key_wire)`` in the COMMON COLLAPSED FORM,
# or ``None`` if the adapter considered the spec empty / unparseable.


def canonicalise_config_validators(parts: list[str]) -> Optional[tuple[frozenset[str], Optional[str]]]:
    """Canonicalise the output of ``_parse_hotkey_parts``.

    ``_parse_hotkey_parts`` returns a flat list of canonical tokens
    (modifiers sorted, then keys in original order). We split it back
    into modifiers and keys using :data:`MODIFIER_ALIASES`.
    """
    if not parts:
        return None
    modifiers: set[str] = set()
    keys: list[str] = []
    for token in parts:
        if token in MODIFIER_ALIASES:
            modifiers.add(MODIFIER_ALIASES[token])
        else:
            keys.append(token)
    if not modifiers and not keys:
        return None
    main_key = keys[0] if keys else None
    return (
        _collapse_modifiers(modifiers),
        _to_wire_name(main_key) if main_key else None,
    )


def canonicalise_pynput(result, Key) -> Optional[tuple[frozenset[str], Optional[str]]]:
    """Canonicalise the output of ``_parse_hotkey_to_pynput``.

    Returns ``None`` if the adapter returned ``None`` (unparseable on
    the current platform — e.g. ``<fn>`` on Linux where pynput has no
    ``Key.fn``).
    """
    if result is None:
        return None

    # Reverse mapping: pynput Key attribute name → canonical modifier name.
    # pynput collapses win/super/cmd → Key.cmd; we map back to "cmd".
    _PYNPUT_TO_CANONICAL_MOD = {
        "ctrl": "ctrl",
        "shift": "shift",
        "alt": "alt",
        "alt_l": "alt",
        "alt_r": "alt_gr",
        "alt_gr": "alt_gr",
        "cmd": "cmd",
        "cmd_l": "cmd",
        "cmd_r": "cmd",
        "fn": "fn",
    }

    def _key_to_name(k) -> Optional[str]:
        # Pynput Key enum members expose their name via .name (enum) or
        # via dir() inspection. We use a simpler heuristic: scan the
        # known attribute set.
        for attr in (
            "ctrl", "ctrl_l", "ctrl_r", "alt", "alt_l", "alt_r", "alt_gr",
            "shift", "shift_l", "shift_r", "cmd", "cmd_l", "cmd_r", "fn",
            "space", "enter", "tab", "esc", "backspace", "delete", "insert",
            "home", "end", "page_up", "page_down", "caps_lock",
            "up", "down", "left", "right",
        ):
            if hasattr(Key, attr) and getattr(Key, attr) is k:
                return attr
        for n in range(1, 25):
            attr = f"f{n}"
            if hasattr(Key, attr) and getattr(Key, attr) is k:
                return attr
        # KeyCode with char
        char = getattr(k, "char", None)
        if char is not None and len(char) == 1:
            return char.lower()
        # KeyCode with vk (function key from from_vk)
        vk = getattr(k, "vk", None)
        if vk is not None and 0x70 <= vk <= 0x87:  # F1-F24 range
            return f"f{vk - 0x6F}"
        return None

    if isinstance(result, tuple):
        modifier_keys, target = result
        mod_names: set[str] = set()
        for mk in modifier_keys:
            for attr, canonical in _PYNPUT_TO_CANONICAL_MOD.items():
                if hasattr(Key, attr) and getattr(Key, attr) is mk:
                    mod_names.add(canonical)
                    break
        main_name = _key_to_name(target)
    else:
        # Single Key/KeyCode — could be a lone modifier or a lone key.
        name = _key_to_name(result)
        if name is None:
            return None
        if name in _PYNPUT_TO_CANONICAL_MOD:
            mod_names = {_PYNPUT_TO_CANONICAL_MOD[name]}
            main_name = None
        else:
            mod_names = set()
            main_name = name

    if not mod_names and not main_name:
        return None
    return (
        _collapse_modifiers(mod_names),
        _to_wire_name(main_name) if main_name else None,
    )


def canonicalise_win32(
    parsed: Optional[tuple[Optional[int], int]],
) -> Optional[tuple[frozenset[str], Optional[str]]]:
    """Canonicalise the output of ``parse_hotkey_to_win32``.

    Returns ``None`` if the adapter returned ``None`` (unparseable —
    e.g. ``<fn>`` has no Win32 equivalent, ``<globe>`` likewise).
    """
    from voice_typer.server.hotkeys import (
        _MOD_ALT, _MOD_ALTGR, _MOD_CONTROL, _MOD_SHIFT, _MOD_WIN,
        _VK_MAP, _init_vk_map,
    )

    if parsed is None:
        return None
    vk, modbits = parsed

    modifiers: set[str] = set()
    if modbits & _MOD_CONTROL:
        modifiers.add("ctrl")
    if modbits & _MOD_SHIFT:
        modifiers.add("shift")
    if modbits & _MOD_ALT:
        modifiers.add("alt")
    if modbits & _MOD_WIN:
        # Win32 collapses win/super/cmd → _MOD_WIN; map back to "cmd"
        # to match the common collapsed form.
        modifiers.add("cmd")
    if modbits & _MOD_ALTGR:
        modifiers.add("altgr")

    main_key: Optional[str] = None
    if vk is not None:
        _init_vk_map()
        # Reverse VK lookup: find the first name mapping to this VK code.
        for name, code in _VK_MAP.items():
            if code == vk:
                main_key = name
                break

    if not modifiers and main_key is None:
        return None
    return (
        _collapse_modifiers(modifiers),
        _to_wire_name(main_key) if main_key else None,
    )


def canonicalise_native(
    d: Optional[dict],
) -> Optional[tuple[frozenset[str], Optional[str]]]:
    """Canonicalise the output of ``parse_hotkey_spec`` (native_hotkeys).

    The dict's ``modifiers`` set is already in the common collapsed
    form (``win`` / ``super`` / ``cmd`` → ``"cmd"``, ``alt_gr`` →
    ``"altgr"``). The ``main_key`` is already a wire-protocol name.
    """
    if d is None:
        return None
    modifiers = frozenset(d["modifiers"])
    main_key = d["main_key"]
    if not modifiers and main_key is None:
        return None
    return (modifiers, main_key)


def canonicalise_canonical(
    spec: HotkeySpec,
) -> Optional[tuple[frozenset[str], Optional[str]]]:
    """Canonicalise the output of the canonical :func:`parse_hotkey`."""
    if spec.is_empty:
        return None
    modifiers = _collapse_modifiers(spec.modifiers)
    main_key = _to_wire_name(spec.main_key) if spec.main_key else None
    if not modifiers and main_key is None:
        return None
    return (modifiers, main_key)


# ─── Test corpus ─────────────────────────────────────────────────────────
#
# ~50 hotkey strings covering simple keys, combos, modifier-only specs,
# and edge cases (empty, whitespace, mixed case, no angle brackets,
# duplicate keys, multi-key combos, alias variants).

CORPUS: list[str] = [
    # ── Simple keys (no modifiers) ──────────────────────────────────
    "<f2>",
    "<f12>",
    "<f24>",
    "<a>",
    "<z>",
    "<0>",
    "<9>",
    "<space>",
    "<enter>",
    "<tab>",
    "<esc>",
    "<backspace>",
    "<insert>",
    "<delete>",
    "<home>",
    "<end>",
    "<page_up>",
    "<page_down>",
    "<caps_lock>",
    "<capslock>",
    "<up>",
    "<down>",
    "<left>",
    "<right>",

    # ── Single-modifier + key combos ────────────────────────────────
    "<ctrl>+<v>",
    "<alt>+<q>",
    "<shift>+<f5>",
    "<ctrl>+<f2>",
    "<alt>+<space>",
    "<shift>+<tab>",

    # ── Multi-modifier + key combos ─────────────────────────────────
    "<ctrl>+<alt>+<v>",
    "<ctrl>+<alt>+<u>",
    "<ctrl>+<shift>+<f1>",
    "<ctrl>+<alt>+<shift>+<f2>",
    "<fn>+<space>",

    # ── Modifier-only ───────────────────────────────────────────────
    "<alt>",
    "<ctrl>",
    "<shift>",
    "<ctrl>+<shift>",
    "<fn>",
    "<globe>",
    "<cmd>",
    "<win>",
    "<super>",

    # ── AltGr variants ──────────────────────────────────────────────
    "<altgr>",
    "<right_alt>",
    "<ralt>",

    # ── Alias variants ──────────────────────────────────────────────
    "<control>+<v>",   # 'control' → 'ctrl'
    "<alt_l>",         # → 'alt'
    "<alt_r>",         # → 'alt'
    "<cmd_l>",         # → 'cmd'
    "<super_l>",       # → 'super'
    "<win_r>",         # → 'win'

    # ── Multi-key combos (extras ignored) ───────────────────────────
    "<a>+<b>",
    "<f2>+<ctrl>+<v>",  # ctrl is modifier; f2 first key; v ignored

    # ── Edge cases ──────────────────────────────────────────────────
    "",
    "   ",
    "<>+<>",
    "++++",
    "<ctrl>+<ctrl>+<v>",  # duplicate modifier
    "<Ctrl>+<ALT>+V",     # mixed case
    "<CTRL>+<ALT>+V",     # uppercase
    "f2",                 # no angle brackets
    "ctrl+alt+v",         # no angle brackets, all lowercase
    "Ctrl+Alt+V",         # no angle brackets, mixed case
]


# Inputs that some adapters legitimately cannot parse on every platform.
# For these, we skip the adapters that return None and only compare the
# adapters that DO produce a result.
#
# ``<fn>`` and ``<globe>``: pynput lacks ``Key.fn`` on Linux/Windows
# (macOS only); Win32 ``RegisterHotKey`` has no Fn / Globe equivalent.
# Both adapters return ``None``, so we skip them.
#
# Any spec containing ``fn`` as a modifier (e.g. ``<fn>+<space>``):
# the pynput adapter silently drops ``fn`` (it can't be expressed as a
# pynput modifier on Linux/Windows), and the Win32 adapter also drops
# it (no ``_MOD_FN`` bit exists). Skipping these adapters for
# fn-containing specs avoids false-positive parity failures.
SKIP_PYNPUT: frozenset[str] = frozenset({
    "<fn>", "<globe>",
})

SKIP_WIN32: frozenset[str] = frozenset({
    "<fn>", "<globe>",
})


def _spec_contains_fn(hotkey: str) -> bool:
    """True if the hotkey spec includes the Fn modifier.

    Used to skip the pynput and win32 adapters for fn-containing specs
    (both adapters silently drop fn — a documented platform limitation).
    """
    spec = parse_hotkey(hotkey)
    return "fn" in spec.modifiers


# ─── Tests ───────────────────────────────────────────────────────────────


class TestParityCorpus:
    """Verify the corpus is non-trivial (~50 entries) and well-formed."""

    def test_corpus_has_at_least_50_entries(self) -> None:
        assert len(CORPUS) >= 50, (
            f"Corpus should have at least 50 entries for meaningful parity "
            f"coverage; got {len(CORPUS)}"
        )

    def test_corpus_has_no_duplicates(self) -> None:
        assert len(CORPUS) == len(set(CORPUS)), (
            "Corpus must not contain duplicates (they waste test time "
            "and obscure coverage)"
        )

    def test_corpus_covers_edge_cases(self) -> None:
        # Empty / whitespace
        assert "" in CORPUS
        assert "   " in CORPUS
        # Angle brackets present
        assert "<f2>" in CORPUS
        # Angle brackets absent
        assert "f2" in CORPUS
        # Mixed case
        assert any(s != s.lower() and s != s.upper() for s in CORPUS), (
            "Corpus should include mixed-case inputs"
        )
        # Uppercase only
        assert any(s.isupper() and s for s in CORPUS if s), (
            "Corpus should include uppercase inputs"
        )
        # Duplicate keys
        assert "<ctrl>+<ctrl>+<v>" in CORPUS
        # Modifier-only
        assert "<alt>" in CORPUS
        assert "<ctrl>+<shift>" in CORPUS
        # Multi-key combo (extras)
        assert "<a>+<b>" in CORPUS


class TestCanonicalParser:
    """Verify the canonical parser's invariants."""

    def test_empty_string_is_empty(self) -> None:
        spec = parse_hotkey("")
        assert spec.is_empty is True
        assert spec.modifiers == ()
        assert spec.keys == ()
        assert spec.main_key is None

    def test_whitespace_only_is_empty(self) -> None:
        spec = parse_hotkey("   ")
        assert spec.is_empty is True

    def test_empty_angle_brackets_is_empty(self) -> None:
        spec = parse_hotkey("<>+<>")
        assert spec.is_empty is True

    def test_modifiers_are_sorted(self) -> None:
        spec = parse_hotkey("<shift>+<ctrl>+<alt>")
        assert spec.modifiers == ("alt", "ctrl", "shift")

    def test_modifiers_are_deduplicated(self) -> None:
        spec = parse_hotkey("<ctrl>+<ctrl>+<v>")
        assert spec.modifiers == ("ctrl",)
        assert spec.keys == ("v",)

    def test_keys_keep_original_order(self) -> None:
        spec = parse_hotkey("<a>+<b>+<c>")
        assert spec.keys == ("a", "b", "c")

    def test_alias_resolution(self) -> None:
        assert parse_hotkey("<control>").modifiers == ("ctrl",)
        assert parse_hotkey("<globe>").modifiers == ("fn",)
        assert parse_hotkey("<altgr>").modifiers == ("alt_gr",)
        assert parse_hotkey("<right_alt>").modifiers == ("alt_gr",)
        assert parse_hotkey("<ralt>").modifiers == ("alt_gr",)
        assert parse_hotkey("<cmd_l>").modifiers == ("cmd",)
        assert parse_hotkey("<super_r>").modifiers == ("super",)
        assert parse_hotkey("<win_l>").modifiers == ("win",)

    def test_distinction_preserved(self) -> None:
        """win/super/cmd are distinct canonical names."""
        assert parse_hotkey("<win>").modifiers == ("win",)
        assert parse_hotkey("<super>").modifiers == ("super",)
        assert parse_hotkey("<cmd>").modifiers == ("cmd",)
        # alt and alt_gr are distinct
        assert parse_hotkey("<alt>").modifiers == ("alt",)
        assert parse_hotkey("<alt_gr>").modifiers == ("alt_gr",)

    def test_main_key_property(self) -> None:
        assert parse_hotkey("<f2>").main_key == "f2"
        assert parse_hotkey("<ctrl>+<alt>+v").main_key == "v"
        assert parse_hotkey("<alt>").main_key is None

    def test_is_modifier_only(self) -> None:
        assert parse_hotkey("<alt>").is_modifier_only is True
        assert parse_hotkey("<ctrl>+<shift>").is_modifier_only is True
        assert parse_hotkey("<f2>").is_modifier_only is False
        assert parse_hotkey("<ctrl>+<v>").is_modifier_only is False

    def test_to_spec_string_roundtrips(self) -> None:
        for s in ("<f2>", "<ctrl>+<alt>+v", "<shift>+<f5>"):
            spec = parse_hotkey(s)
            # Round-trip: parse the spec string back, should be equal.
            assert parse_hotkey(spec.to_spec_string()) == spec


class TestAdapterParity:
    """Verify all four adapters produce identical canonical forms.

    For each input in :data:`CORPUS`, we run all four parsers and
    canonicalise their outputs to a common collapsed form. We then
    assert that every adapter that produced a non-None result agrees
    with the canonical parser.
    """

    @pytest.fixture(scope="class")
    @classmethod
    def fake_pynput(cls):
        """A fake pynput Key/KeyCode for testing without a display.

        ``_parse_hotkey_to_pynput`` takes ``Key`` and ``KeyCode`` as
        parameters (so the module doesn't import pynput at module
        load time). We pass mocks that emulate the parts of the pynput
        API the function uses: ``hasattr(Key, name)``,
        ``getattr(Key, name)``, ``KeyCode.from_char(c)``, and
        ``KeyCode.from_vk(vk)``.
        """
        class _FakeKey:
            def __init__(self, name: str):
                self.name = name
            def __repr__(self) -> str:
                return f"Key.{self.name}"
            def __eq__(self, other) -> bool:
                return isinstance(other, _FakeKey) and self.name == other.name
            def __hash__(self) -> int:
                return hash(("Key", self.name))

        class _FakeKeyCode:
            def __init__(self, *, char: Optional[str] = None, vk: Optional[int] = None):
                self.char = char
                self.vk = vk
            def __repr__(self) -> str:
                if self.char is not None:
                    return f"KeyCode(char={self.char!r})"
                return f"KeyCode(vk={self.vk!r})"
            def __eq__(self, other) -> bool:
                if not isinstance(other, _FakeKeyCode):
                    return False
                return self.char == other.char and self.vk == other.vk
            def __hash__(self) -> int:
                return hash(("KeyCode", self.char, self.vk))
            @classmethod
            def from_char(cls, c: str) -> "_FakeKeyCode":
                return cls(char=c)
            @classmethod
            def from_vk(cls, vk: int) -> "_FakeKeyCode":
                return cls(vk=vk)

        class _FakeKeyEnum:
            def __init__(self):
                # Mirror the pynput Key enum members used by the adapter.
                for name in (
                    "ctrl", "ctrl_l", "ctrl_r",
                    "alt", "alt_l", "alt_r", "alt_gr",
                    "shift", "shift_l", "shift_r",
                    "cmd", "cmd_l", "cmd_r",
                    "fn",
                    "space", "enter", "tab", "esc", "backspace",
                    "delete", "insert", "home", "end",
                    "page_up", "page_down", "caps_lock",
                    "up", "down", "left", "right",
                ):
                    setattr(self, name, _FakeKey(name))
                for n in range(1, 25):
                    setattr(self, f"f{n}", _FakeKey(f"f{n}"))

            def __getattr__(self, name: str):
                raise AttributeError(name)

        return _FakeKeyEnum(), _FakeKeyCode

    @pytest.mark.parametrize("hotkey", CORPUS)
    def test_all_adapters_agree_with_canonical(
        self, hotkey: str, fake_pynput
    ) -> None:
        """All four adapters must produce the same canonical form."""
        Key, KeyCode = fake_pynput

        # Compute the expected (collapsed) form from the canonical parser.
        expected = canonicalise_canonical(parse_hotkey(hotkey))

        # Run each adapter.
        from voice_typer.server.config_validators import _parse_hotkey_parts
        from voice_typer.server.hotkeys import (
            _parse_hotkey_to_pynput,
            parse_hotkey_to_win32,
        )
        from voice_typer.server.native_hotkeys import parse_hotkey_spec

        cv_parts = _parse_hotkey_parts(hotkey)
        pp_result = _parse_hotkey_to_pynput(hotkey, Key, KeyCode)
        w32_result = parse_hotkey_to_win32(hotkey)
        native_dict = parse_hotkey_spec(hotkey)

        cv_canon = canonicalise_config_validators(cv_parts)
        pp_canon = canonicalise_pynput(pp_result, Key)
        w32_canon = canonicalise_win32(w32_result)
        native_canon = canonicalise_native(native_dict)

        # The config_validators adapter never returns None for non-empty
        # inputs (it returns [] for empty, which canonicalises to None).
        # The pynput and win32 adapters can return None for inputs they
        # can't handle (e.g. <fn> on Linux). For those, skip the adapter.
        skip_pynput = hotkey in SKIP_PYNPUT or _spec_contains_fn(hotkey)
        skip_win32 = hotkey in SKIP_WIN32 or _spec_contains_fn(hotkey)
        results: list[tuple[str, Optional[tuple[frozenset[str], Optional[str]]]]] = [
            ("canonical", expected),
            ("config_validators", cv_canon),
            ("native_hotkeys", native_canon),
        ]
        if not skip_pynput:
            results.append(("pynput", pp_canon))
        if not skip_win32:
            results.append(("win32", w32_canon))

        # All non-None results must agree.
        non_none = [(name, r) for name, r in results if r is not None]
        if not non_none:
            # All adapters returned None — the spec is genuinely empty.
            assert expected is None, (
                f"For {hotkey!r}: all adapters returned None but canonical "
                f"parser produced {expected!r}"
            )
            return

        # Compare all non-None results.
        first_name, first = non_none[0]
        for name, result in non_none[1:]:
            assert result == first, (
                f"For {hotkey!r}: adapter {name!r} produced {result!r} "
                f"but adapter {first_name!r} produced {first!r}. "
                f"(canonical expected: {expected!r})"
            )

        # And the agreed result must match the canonical parser's output
        # (if the canonical parser produced a non-None result).
        if expected is not None:
            assert first == expected, (
                f"For {hotkey!r}: adapters agreed on {first!r} but "
                f"canonical parser produced {expected!r}"
            )


class TestEdgeCaseParity:
    """Edge-case parity tests beyond the corpus."""

    def test_empty_string_all_adapters_return_none(self) -> None:
        """Empty string: all adapters agree the spec is empty."""
        from voice_typer.server.config_validators import _parse_hotkey_parts
        from voice_typer.server.native_hotkeys import parse_hotkey_spec

        assert _parse_hotkey_parts("") == []
        assert parse_hotkey_spec("") is None

    def test_whitespace_only_all_adapters_return_none(self) -> None:
        from voice_typer.server.config_validators import _parse_hotkey_parts
        from voice_typer.server.native_hotkeys import parse_hotkey_spec

        assert _parse_hotkey_parts("   ") == []
        assert parse_hotkey_spec("   ") is None

    def test_mixed_case_normalises_identically(self) -> None:
        """``<Ctrl>+<ALT>+V`` and ``<ctrl>+<alt>+v`` parse identically."""
        a = parse_hotkey("<Ctrl>+<ALT>+V")
        b = parse_hotkey("<ctrl>+<alt>+v")
        c = parse_hotkey("<CTRL>+<ALT>+V")
        assert a == b == c
        assert a.modifiers == ("alt", "ctrl")
        assert a.keys == ("v",)

    def test_angle_brackets_optional(self) -> None:
        """``f2`` and ``<f2>`` parse identically."""
        assert parse_hotkey("f2") == parse_hotkey("<f2>")
        assert parse_hotkey("ctrl+alt+v") == parse_hotkey("<ctrl>+<alt>+v")

    def test_duplicate_modifiers_deduplicated(self) -> None:
        spec = parse_hotkey("<ctrl>+<ctrl>+<ctrl>+<v>")
        assert spec.modifiers == ("ctrl",)
        assert spec.keys == ("v",)

    def test_modifier_only_combos(self) -> None:
        from voice_typer.server.config_validators import _parse_hotkey_parts
        from voice_typer.server.native_hotkeys import parse_hotkey_spec

        # <alt>
        spec = parse_hotkey("<alt>")
        assert spec.is_modifier_only is True
        assert spec.main_key is None

        # config_validators adapter
        parts = _parse_hotkey_parts("<alt>")
        assert parts == ["alt"]

        # native_hotkeys adapter
        d = parse_hotkey_spec("<alt>")
        assert d is not None
        assert d["is_modifier_only"] is True
        assert d["main_key"] is None
        assert d["modifiers"] == {"alt"}

    def test_multi_key_combo_extras_ignored(self) -> None:
        """``<a>+<b>``: main_key is 'a', extra 'b' is ignored."""
        from voice_typer.server.native_hotkeys import parse_hotkey_spec

        spec = parse_hotkey("<a>+<b>")
        assert spec.keys == ("a", "b")
        assert spec.main_key == "a"

        d = parse_hotkey_spec("<a>+<b>")
        assert d is not None
        assert d["main_key"] == "A"  # wire-protocol name

    def test_no_other_module_has_its_own_alias_table(self) -> None:
        """RW-1 constraint: MODIFIER_ALIASES in hotkey_spec.py is the
        single source of truth for SPEC-PARSING alias resolution. No
        other server module should duplicate the alias-resolution dict
        (i.e. a dict whose keys are alias names like ``"control"``,
        ``"altgr"``, ``"right_alt"``, etc. and whose values are the
        canonical names they resolve to).

        This does NOT prohibit:

        - Display maps (e.g. ``tray_hotkey._DISPLAY_MAP``) that map
          canonical names to user-facing display strings — those are
          a separate concern (display, not parsing).
        - Wire-canonical maps (e.g.
          ``native_hotkeys._canonical_modifier_name_for_token``) that
          map spec-side canonical names to wire-side canonical names
          for cross-platform modifier matching — those are a separate
          concern (wire matching, not parsing).
        - Platform-specific collapse tables (e.g. the
          ``_CANONICAL_TO_MODBIT`` dict inside
          ``parse_hotkey_to_win32``) that map canonical names to
          platform-specific bit flags — those are a separate concern
          (platform adaptation, not parsing).

        To distinguish a true spec-parsing alias table from these
        related-but-different dicts, we require the dict to have at
        least 8 of the alias-signature keys (``control``, ``altgr``,
        ``right_alt``, ``ralt``, ``super_l``, ``super_r``, ``win_l``,
        ``win_r``, ``cmd_l``, ``cmd_r``, ``globe``). A display map
        has at most 3 of these; a wire-canonical map has at most 1;
        the canonical ``MODIFIER_ALIASES`` has all 11.
        """
        import ast
        from pathlib import Path

        server_dir = Path(__file__).resolve().parent.parent / "voice_typer" / "server"
        alias_signatures = {
            "control", "altgr", "right_alt", "ralt",
            "super_l", "super_r", "win_l", "win_r",
            "cmd_l", "cmd_r", "globe",
        }
        offenders: list[str] = []

        for py in server_dir.glob("*.py"):
            if py.name == "hotkey_spec.py":
                continue  # this is the canonical module
            try:
                tree = ast.parse(py.read_text(encoding="utf-8"))
            except SyntaxError:
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.Dict):
                    keys: list[str] = []
                    for k in node.keys:
                        if isinstance(k, ast.Constant) and isinstance(k.value, str):
                            keys.append(k.value)
                    overlap = alias_signatures & set(keys)
                    # Require at least 8 alias-signature keys to flag —
                    # this excludes display maps (≤3 overlap) and
                    # wire-canonical maps (≤1 overlap) while still
                    # catching a true duplicate alias table (≥8 overlap).
                    if len(overlap) >= 8:
                        offenders.append(
                            f"{py.name}: dict with {len(overlap)} alias-like keys "
                            f"({sorted(overlap)})"
                        )

        assert not offenders, (
            "RW-1 violation: found spec-parsing alias dict(s) outside "
            "hotkey_spec.py (the single source of truth): "
            + "; ".join(offenders)
        )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
