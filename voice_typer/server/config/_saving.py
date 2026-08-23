"""Config save-path implementations (atomic write + ACL + warmup).

Extracted from ``config/__init__.py`` (W1-A2 / W3-A5 continuation of
the monolith split). Hosts the bodies behind the ``Config`` save-path
methods (which live as thin delegators on the
``_ConfigLifecycleMixin`` in ``config/_lifecycle.py``):

- :func:`_save_impl` — ``Config.save()`` body (cross-process lock +
  never-raises error mapping + Windows dir-ACL tightening),
- :func:`_save_with_mutation_lock_impl` — in-process RLock wrapper,
- :func:`_save_unlocked_impl` — the atomic write itself (dirty-flag +
  byte-identical short-circuits, credential-store secret routing,
  best-effort ``config.json.bak`` backup),
- :func:`_save_strict_impl` — raising variant,
- :func:`_warmup_keyring_probe_impl` — one-time keyring probe,
- :func:`_enforce_windows_owner_only_acl` — Windows icacls lockdown.

Import-safety / monkeypatch contract: this module is imported at the
TOP of ``config/__init__.py``. Every name that lives in the
``config`` package namespace (``_config_dir``, ``_secure_read_text``,
``_secure_atomic_write``, ``_acquire_config_lock``,
``_enforce_windows_owner_only_acl``, ``is_windows``,
``_windows_owner_only_acl_verified``, ``_warmup_called``) is looked
up lazily via ``import voice_typer.server.config as _cfg`` inside the
function body so tests that monkeypatch
``voice_typer.server.config.<name>`` keep taking effect (same
convention as ``config/_migration.py`` and ``config/loader.py``).
"""

import json
import logging
import os
from dataclasses import asdict
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover — typing-only, never imported at runtime
    from pathlib import Path

    from voice_typer.server.config import Config

log = logging.getLogger("voice_typer.server.config")


def _enforce_windows_owner_only_acl(path: "Path | str") -> bool:
    """On Windows, restrict file/dir ACL to the current user only.

    Uses ``icacls`` to remove inherited ACEs (``/inheritance:r``) and
    grant the current user full control (``/grant:r``). This is a
    defense-in-depth measure so a ``config.json`` (which may contain
    plaintext API keys when the OS keyring is unavailable) in a shared
    ``%APPDATA%`` or ``VOICE_TYPER_CONFIG_DIR`` is not world-readable.
    ``tempfile.mkstemp`` inside ``_secure_atomic_write`` inherits the
    parent dir's DACL on Windows, so if the config dir is shared, the
    temp file (and thus the final ``config.json`` after ``os.replace``)
    inherits that shared DACL — making the plaintext API keys
    world-readable. Calling this helper after every config write
    re-tightens the ACL to owner-only.

    Fast path (the config-dir verification cache):
    ``tempfile.mkstemp`` creates files that INHERIT the parent
    directory's DACL. ``Config.save()`` tightens the config dir's ACL
    once, on the first save of this process, and records the dir in the
    ``config`` module's ``_windows_owner_only_acl_verified`` set (the
    path is only cached when the dir-wide icacls SUCCEEDS). Once a
    directory is verified owner-only, every file created inside it
    afterwards (``config.json``, ``config.json.bak``) is automatically
    owner-only, so re-running ``icacls`` per file is redundant. This
    function skips the ~210ms ``icacls`` subprocess for any path whose
    parent dir is in the verified set, returning ``True`` immediately.
    A dir that could NOT be tightened is never cached, so per-file
    enforcement keeps running there (defense-in-depth preserved).

    Best-effort: logs a warning on failure but does NOT raise, so a
    permission-restricted environment (e.g. ``icacls`` not on PATH,
    user lacks WRITE_DAC, etc.) doesn't break ``save()``. The log
    message is truncated to 200 chars to avoid log bloat from
    multi-line ``icacls`` output.

    No-op on non-Windows (POSIX uses ``os.chmod(path, 0o600)``
    elsewhere in the save path).

    Args:
        path: filesystem path (file or directory) to lock down.

    Returns:
        ``True`` if the ACL is (or is now) owner-only; ``False`` if
        enforcement could not be confirmed (a failed ``icacls``
        returns ``False`` so callers can choose NOT to mark the dir
        as verified).
    """
    import voice_typer.server.config as _cfg

    if not _cfg.is_windows():
        return True
    # Fast path: files inside a dir we already tightened inherit the
    # owner-only DACL — no subprocess needed. Avoids ~420ms of icacls
    # subprocess overhead per save (2 calls/save) that made concurrent
    # saves exceed the cross-process lock deadline.
    parent_dir = str(_cfg.Path(path).parent)
    if parent_dir in _cfg._windows_owner_only_acl_verified:
        return True
    import subprocess

    username = os.environ.get("USERNAME") or os.environ.get("USER")
    if not username:
        log.warning(
            "[CONFIG] cannot enforce Windows ACL on %s: USERNAME env var is empty",
            path,
        )
        return False
    try:
        # /inheritance:r — remove all inherited ACEs
        # /grant:r      — replace (not merge) explicit grants
        # "<user>:F"    — Full control to the current user only
        # Using a list (not a shell string) sidesteps cmd.exe
        # metacharacter injection even if USERNAME contains shell
        # specials.
        cmd = [
            "icacls",
            str(path),
            "/inheritance:r",
            "/grant:r",
            f"{username}:F",
        ]
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        if result.returncode != 0:
            log.warning(
                "[CONFIG] icacls ACL enforcement failed on %s (rc=%d): %s",
                path,
                result.returncode,
                (result.stderr or "").strip()[:200],
            )
            return False
        return True
    except (OSError, subprocess.SubprocessError) as e:
        log.warning(
            "[CONFIG] icacls ACL enforcement error on %s: %s",
            path,
            e,
        )
        return False


def _save_impl(cfg: "Config") -> bool:
    """Body of ``Config.save()`` — see the delegator's docstring.

    Save config to disk atomically via temp file + os.replace.
    Returns True on success, False on failure. Errors are logged but
    never raised (the IPC ``set_config`` path relies on the
    never-raises contract). Acquires the cross-process config lock
    around :func:`_save_with_mutation_lock_impl`.
    """
    import voice_typer.server.config as _cfg

    # Windows-only, best-effort: tighten the config DIR's ACL when
    # this save CREATES the directory. We cannot run ``icacls <dir>
    # /inheritance:r`` on an existing dir — while ANY file in it is
    # held open (``config.json.lock`` during every save), the ACL
    # rewrite poisons the open file on Python < 3.11.13 (where
    # ``os.open`` lacks ``FILE_SHARE_DELETE`` on Windows), failing
    # every subsequent ``Config.save()`` in the process with
    # ``PermissionError`` (reproduced on 3.11.9 — the CI 3.11 leg
    # failed ~20 config tests); the same rewrite on a dir that
    # already contains files breaks writes to those files too. A
    # directory we just created is guaranteed empty, so the icacls
    # there is safe and every file created afterwards
    # (``config.json.lock``, the ``tempfile.mkstemp`` tmp,
    # ``config.json``, ``config.json.bak``) inherits the owner-only
    # dir DACL. Note this is narrow belt-and-suspenders: in the
    # normal flow the config dir is created by logging/history init
    # BEFORE the first save (and per-user ``%APPDATA%`` is
    # owner-only by default anyway) — the meaningful hardening is
    # the per-file icacls on ``config.json`` / ``config.json.bak``
    # in ``_save_unlocked``. Guarded by the same dirty-flag
    # short-circuit as ``_save_unlocked`` so no-op saves skip it;
    # the broad catch keeps ``save()``'s never-raises contract even
    # if ``_config_dir()`` raises in an edge scenario.
    if _cfg.is_windows() and (cfg._dirty or cfg._last_saved_bytes is None):
        try:
            config_dir = _cfg._config_dir()
            config_dir.mkdir(parents=True, exist_ok=True)
            # Tighten the config DIR's ACL on the FIRST save of this
            # process, whether or not this call created the dir
            # (Config.__init__ may already have created it, or a
            # prior run left it behind). The path is only cached
            # when the dir-wide icacls SUCCEEDS, so a dir that can't
            # be tightened keeps per-file enforcement. Once verified,
            # every file created by mkstemp inside the dir inherits
            # the owner-only DACL, so the per-file icacls calls in
            # _save_unlocked become cheap no-ops (see
            # _enforce_windows_owner_only_acl fast path). Skipping
            # re-verification avoids re-running dir-wide icacls on
            # an existing dir.
            if str(config_dir) not in _cfg._windows_owner_only_acl_verified and _cfg._enforce_windows_owner_only_acl(
                config_dir
            ):
                _cfg._windows_owner_only_acl_verified.add(str(config_dir))
        except Exception:
            # Best-effort hardening — never block the save (see the
            # never-raises contract in the ``save`` docstring).
            pass
    try:
        with _cfg._acquire_config_lock():
            return cfg._save_with_mutation_lock()
    except TimeoutError as e:
        log.warning("[CONFIG] %s", e)
        return False
    except (OSError, PermissionError) as e:
        log.error("[CONFIG] Failed to save config: %s", e)
        return False
    except (TypeError, ValueError) as e:
        # ``json.dumps`` (called inside
        # :func:`_save_unlocked_impl` via ``asdict(self)``) can raise
        # ``TypeError`` when a field holds a non-JSON-serializable
        # value (e.g. a ``set`` / ``datetime`` / custom object
        # smuggled in via ``setattr`` or a botched migration), and
        # ``ValueError`` for circular references. The previous
        # ``except`` tuple only caught ``TimeoutError`` /
        # ``OSError`` / ``PermissionError`` — a ``TypeError``
        # propagated to the caller, violating the ``save()``
        # docstring's "never raises" contract (which the IPC
        # ``set_config`` path relies on: a ``TypeError`` would
        # crash the IPC handler thread instead of returning a
        # ``False`` ack the renderer can surface as a save-failed
        # toast). Widen the tuple to include both serialization
        # failure modes and return ``False`` (the underlying
        # ``OSError``/``TypeError`` is logged at ERROR so the
        # operator can diagnose which field is non-serializable).
        log.error("[CONFIG] Failed to serialize config for save: %s", e)
        return False


def _save_with_mutation_lock_impl(cfg: "Config") -> bool:
    """Body of ``Config._save_with_mutation_lock``.

    Acquire the mutation lock (if set) and delegate to
    :func:`_save_unlocked_impl`.

    Assumes the cross-process file lock is already held (caller
    ``save`` acquires it). The mutation lock is an in-process
    ``RLock`` that serialises concurrent ``save()`` calls from
    different threads within THIS process (the cross-process file
    lock only serialises across processes).
    """
    lock = cfg._mutation_lock
    if lock is None:
        return cfg._save_unlocked()
    with lock:
        return cfg._save_unlocked()


def _save_unlocked_impl(cfg: "Config") -> bool:
    """Body of ``Config._save_unlocked`` — assumes both locks held.

    Best-effort single-slot backup of the existing config.json BEFORE
    we overwrite it. The backup preserves the EXACT bytes that were on
    disk (byte-for-byte) so the user can manually recover dropped
    fields after a downgrade save.

    When the in-memory serialized content matches the
    previously-persisted bytes (``_last_saved_bytes``), the entire
    backup block is skipped — no ``Path.read_bytes`` call, no
    ``config.json.bak`` write, no ``os.chmod``. This is the common
    case for ``set_config`` round-trips that don't change any
    persisted field.

    A ``_dirty`` flag (set True by ``__setattr__`` on every
    persisted-field mutation, set False after a successful save)
    is checked at the TOP of this function. When False AND
    ``_last_saved_bytes`` is populated, the entire save is
    short-circuited BEFORE the expensive ``asdict(self)`` +
    ``json.dumps`` calls — the common case for back-to-back
    ``save()`` calls with no intervening mutation (e.g. a
    ``set_config`` IPC round-trip whose ``updates`` dict was a
    no-op after the per-key dirty-check in ``apply_config``).
    """
    import voice_typer.server.config as _cfg

    # Dirty-flag short-circuit. If no persisted field has
    # been mutated since the last successful save (and we have in
    # fact saved at least once), there is nothing to do — skip the
    # entire save including ``asdict(self)`` + ``json.dumps`` +
    # ``_secure_atomic_write`` + ``.bak`` write. The ``_dirty`` flag
    # is set True by ``__setattr__`` on every persisted-field
    # mutation and set False at the bottom of this function after a
    # successful write. The ``_last_saved_bytes is not None`` guard
    # ensures a fresh ``Config()`` (which has ``_dirty=True`` from
    # ``__post_init__``) always falls through to the real write on
    # its first save — even if ``_dirty`` were manually cleared,
    # the cache would still be ``None`` and the guard below would
    # fall through. Belt-and-suspenders.
    if not cfg._dirty and cfg._last_saved_bytes is not None:
        return True
    path = _cfg._config_dir()
    path.mkdir(parents=True, exist_ok=True)
    if not _cfg.is_windows():
        try:
            os.chmod(path, 0o700)
        except OSError as e:
            log.warning("[CONFIG] Failed to chmod config dir: %s", e)
    # The config DIR's ACL is tightened in ``save()`` BEFORE the
    # cross-process lock is acquired, NOT here — this function is
    # always called with ``config.json.lock`` held open, and
    # running ``icacls <dir> /inheritance:r`` while the lock file
    # is open poisons it on Python < 3.11.13 (``os.open`` lacks
    # ``FILE_SHARE_DELETE`` on Windows), failing every subsequent
    # save() in the process. The secret-holding files
    # (``config.json`` and ``config.json.bak``) are tightened
    # individually after each write below.
    config_file = path / "config.json"
    data = asdict(cfg)
    # Reset the ``_secrets_routed_in_save`` flag at the
    # start of the routing block. Set to True below ONLY if the
    # routing try-block completes (whether keyring was available
    # or not — the routing was "attempted" and the secret is
    # either in keyring or persisted as plaintext in config.json
    # by the final ``_secure_atomic_write``). Readers
    # (``config_applier.apply_config``) check this flag to decide
    # whether to run a redundant ``store_secret`` loop after
    # ``save_strict`` succeeds; the loop only runs when routing
    # did NOT happen (e.g. ``Config.save`` was mocked to skip
    # routing in a test).
    object.__setattr__(cfg, "_secrets_routed_in_save", False)
    # route API key fields through credential_store.
    try:
        from voice_typer.server import credential_store

        if credential_store.is_keyring_available():
            for provider, field_name in credential_store.PROVIDER_TO_CONFIG_FIELD.items():
                value = data.get(field_name, "")
                # defensive type guard for non-string api_key
                # values. ``asdict(self)`` reflects whatever the
                # in-memory Config instance carries — normally a
                # str (the dataclass field type) but a buggy IPC
                # caller or a monkeypatched test instance could
                # set a non-string value, which would crash here
                # with AttributeError on ``.startswith()`` (and
                # propagate up through ``Config.save``'s outer
                # ``except Exception``, logging a warning and
                # aborting the entire save).
                #
                # Coerce int/float (excluding bool, which is a
                # subclass of int in Python) to str — backward
                # compat with old configs that stored api_key as
                # an int. Skip other non-string truthy types
                # (dict, list) with a warning so the save can
                # proceed for the remaining providers.
                if not isinstance(value, str):
                    if not value:
                        # Falsy (None, 0, [], {}, "") — nothing
                        # to route to credential_store.
                        continue
                    if isinstance(value, (int, float)) and not isinstance(value, bool):
                        log.warning(
                            "[CONFIG] DE-23: %s field has non-string value (type=%s) — coercing to str",
                            field_name,
                            type(value).__name__,
                        )
                        value = str(value)
                        data[field_name] = value
                    else:
                        log.warning(
                            "[CONFIG] DE-23: %s field has non-string value (type=%s)"
                            " — skipping credential_store routing",
                            field_name,
                            type(value).__name__,
                        )
                        continue
                if value and not value.startswith(credential_store.KEYRING_REF_PREFIX):
                    # pass ``_caller_holds_config_lock=True``
                    # so ``store_secret`` → ``_write_plaintext_fallback`` does
                    # NOT re-acquire ``config.json.lock`` (which would deadlock
                    # — fcntl.flock is per-open-file-description on Linux, so a
                    # second ``open()`` + ``flock(LOCK_EX | LOCK_NB)`` on the
                    # same lock file from THIS process fails with EWOULDBLOCK
                    # and spins until the 5s ``_CONFIG_LOCK_TIMEOUT_SECONDS``
                    # deadline, then raises TimeoutError — pre-fix, that was
                    # caught by ``_write_plaintext_fallback``'s broad
                    # ``except Exception`` and logged at ERROR, silently
                    # dropping the user's API key when keyring failed mid-save).
                    # We're inside ``_save_unlocked`` (caller
                    # ``save`` acquired the cross-process lock via
                    # ``with _acquire_config_lock():``), so the lock IS held.
                    # Check the return value: store_secret returns False when
                    # keyring was probed "available" but set_password transiently
                    # fails and falls back to _write_plaintext_fallback. In that
                    # case, leave data[field_name] as the plaintext value so the
                    # final _secure_atomic_write below persists it in one write —
                    # simultaneously (a) eliminating redundant per-provider
                    # read-modify-write cycles and (b) preserving the secret on
                    # disk (previously the reference token overwrite caused silent
                    # API-key data loss when keyring flaked mid-save).
                    stored_to_keyring = credential_store.store_secret(
                        provider, value, _caller_holds_config_lock=True
                    )
                    if stored_to_keyring:
                        data[field_name] = f"{credential_store.KEYRING_REF_PREFIX}{provider}"
                    # else: leave data[field_name] as the plaintext value —
                    # the final _secure_atomic_write will persist it.
        # Routing was attempted (keyring available OR not —
        # if not available, the plaintext value is persisted by
        # the final ``_secure_atomic_write`` below, which is the
        # equivalent "routing" for the no-keyring path). Signal
        # ``config_applier.apply_config`` that its redundant
        # ``store_secret`` loop can be skipped.
        object.__setattr__(cfg, "_secrets_routed_in_save", True)
    except Exception as e:
        # log only the exception TYPE (not the message) —
        # credential_store exceptions can echo the secret value
        # being stored, which would leak into log files.
        log.warning(
            "[CONFIG] credential_store routing failed: %s — writing config with current api_key values",
            type(e).__name__,
        )
        # Leave ``_secrets_routed_in_save`` at False (set
        # above before the try-block) so ``apply_config``'s
        # redundant ``store_secret`` loop runs as a safety net.
    content = json.dumps(data, indent=2)
    content_bytes = content.encode("utf-8")

    # Skip the write ENTIRELY when the new content matches the
    # previously-persisted bytes. The ``_last_saved_bytes`` cache
    # is populated after each successful ``save()`` and is ``None``
    # on a fresh ``Config()`` instance (set in ``__post_init__``).
    # When the cache is populated and the new ``content_bytes`` match
    # it, there is nothing to do — the on-disk file already has the
    # exact bytes we would write. This mirrors the
    # ``PersistedJSON._last_written_bytes`` pattern in
    # ``secure_file_io.py`` (load → cache, save → diff → skip). The
    # common case is a ``set_config`` IPC round-trip that doesn't
    # change any persisted field (e.g. the renderer echoes back the
    # same config the server already has): without this skip, every
    # such call paid the full ``_secure_atomic_write`` cost (temp
    # file, ``os.replace``, optional fsync) plus the
    # ``config.json.bak`` backup read+write — pure I/O churn for an
    # identical result. The ``is not None`` guard ensures a fresh
    # instance (cache never populated) always falls through to the
    # real write, so the first save after construction/load is
    # never skipped.
    #
    # Note: the ``_dirty`` short-circuit at the top of this
    # function already handles the common case (no mutation since
    # last save). This byte-level check is a SECOND layer of
    # defense: it catches the rare case where ``_dirty`` is True
    # (a field was mutated) but the mutation is a no-op (e.g.
    # ``cfg.hotkey = cfg.hotkey``) or the field was mutated and
    # then mutated back. Without this check, those no-op
    # mutations would trigger a full write unnecessarily.
    if cfg._last_saved_bytes is not None and cfg._last_saved_bytes == content_bytes:
        # Clear the dirty flag here too — the content
        # matches what's on disk, so the in-memory state is
        # effectively "clean" relative to disk.
        object.__setattr__(cfg, "_dirty", False)
        return True

    # Short-circuit the entire backup block when the new
    # content matches the previously-persisted bytes. The cached
    # bytes are only updated after a successful write below, so a
    # previous failed save (or a fresh Config() that has never
    # saved) falls through to the full backup path.
    #
    # When ``_last_saved_bytes`` is populated, use it directly as
    # ``existing_bytes`` instead of re-reading ``config.json`` via
    # ``_secure_read_text``. The cache reflects the exact bytes we
    # wrote on the last successful save, which (barring external
    # modification) equals the current on-disk content. This skips
    # one filesystem read (the ``_secure_read_text`` open + read +
    # inode-verify) per modified save — the .bak WRITE still
    # happens (the content has changed, so the backup is needed),
    # but the READ is eliminated. The ``_secure_read_text`` path
    # is retained as a fallback for the first save (cache is
    # ``None``) so the symlink-TOCTOU-safe read is still used
    # when we have no cached bytes to compare against.
    if cfg._last_saved_bytes != content_bytes and config_file.exists():
        # best-effort backup before overwrite.
        try:
            if cfg._last_saved_bytes is not None:
                # Use the cached bytes from the last
                # successful save. This is the bytes-identical
                # content we wrote last time; barring external
                # modification it equals the current on-disk
                # content. Skips the ``_secure_read_text`` open +
                # read + inode-verify.
                existing_bytes = cfg._last_saved_bytes
                existing_text = existing_bytes.decode("utf-8")
            else:
                # Fallback: first save (cache is None) —
                # read the existing config.json via
                # ``_secure_read_text`` (O_NOFOLLOW + inode
                # re-verify) instead of ``config_file.read_bytes()``
                # which calls ``open()`` internally and FOLLOWS
                # SYMLINKS. A local attacker who replaces
                # config.json with a symlink to ~/.bashrc between
                # saves would otherwise get ~/.bashrc content
                # copied into config.json.bak (info disclosure via
                # the .bak). The subsequent ``_secure_atomic_write``
                # uses ``os.replace`` which replaces the SYMLINK
                # itself (safe), so the actual config.json write
                # is fine — but the .bak was already poisoned.
                existing_text = _cfg._secure_read_text(config_file)
                existing_bytes = existing_text.encode("utf-8")
            if existing_bytes != content_bytes:
                bak_path = path / "config.json.bak"
                # also route the .bak WRITE through
                # ``_secure_atomic_write`` so the destination path
                # is created with O_NOFOLLOW (no symlink-following
                # on the destination either) + fsync + 0o600 perms.
                _cfg._secure_atomic_write(bak_path, existing_text)
                if not _cfg.is_windows():
                    try:
                        os.chmod(bak_path, 0o600)
                    except OSError as e:
                        log.debug("[CONFIG] Failed to chmod config.json.bak: %s", e)
                else:
                    # enforce owner-only ACL on the
                    # backup file on Windows — it contains the
                    # same plaintext API keys as config.json.
                    _cfg._enforce_windows_owner_only_acl(bak_path)
        except (OSError, ValueError) as e:
            # OSError covers filesystem errors; ValueError covers
            # the SEC-002 inode-changed-during-read guard (symlink
            # TOCTOU detection). Both are best-effort failures —
            # the actual config.json write (below) still proceeds.
            log.debug(
                "[CONFIG] Failed to back up existing config.json to config.json.bak: %s",
                e,
            )

    _cfg._secure_atomic_write(config_file, content)
    if _cfg.is_windows():
        # ``_secure_atomic_write`` creates the temp
        # file via ``tempfile.mkstemp``, which on Windows
        # inherits the parent dir's DACL. If the config dir is
        # shared, ``config.json`` (with plaintext API keys when
        # keyring is unavailable) becomes world-readable. Re-tighten
        # the ACL on the destination after the rename.
        _cfg._enforce_windows_owner_only_acl(config_file)
    # record the bytes we just persisted so the next
    # identical save can short-circuit the backup block above.
    # Updated only AFTER a successful write — a failed write
    # leaves the cache stale, which forces the next save through
    # the full backup path (safe-but-slower fallback).
    object.__setattr__(cfg, "_last_saved_bytes", content_bytes)
    # Clear the dirty flag — the in-memory state now
    # matches the on-disk state. The next ``save()`` call (with no
    # intervening mutation) will short-circuit at the top of this
    # function via the ``not self._dirty`` check.
    object.__setattr__(cfg, "_dirty", False)
    return True


def _save_strict_impl(cfg: "Config") -> None:
    """Body of ``Config.save_strict`` — save; raise on failure.

    Wraps ``save()`` and raises :class:`RuntimeError` if the
    underlying save returned ``False`` (which indicates an
    ``OSError`` or ``PermissionError`` was caught and logged by
    ``save()``). Callers who care about persistence — i.e. IPC
    handlers that return an ``ack`` to the renderer only when the
    config actually landed on disk — call this instead of
    ``save()`` so a silent disk failure is surfaced as an IPC error
    rather than a successful-but-empty ack.

    The error message is intentionally generic (it does NOT embed
    the underlying ``OSError`` message) because the renderer may
    display the error string to the user — the underlying message
    could contain a filesystem path that we don't want to leak
    across the IPC boundary. ``save()`` already logs the full
    error message on the server side.

    ``apply_config`` (in ``config_applier.py``) and
    ``reset_config_to_defaults`` (in ``service/config_service.py``)
    both call ``save_strict()`` so a silent disk failure is
    surfaced as an IPC error rather than a successful-but-empty
    ack.
    """
    ok = cfg.save()
    if not ok:
        raise RuntimeError("failed to persist config to disk")


def _warmup_keyring_probe_impl() -> None:
    """Body of ``Config._warmup_keyring_probe``.

    Eagerly probe ``credential_store.is_keyring_available()``
    once at app startup so the FIRST ``save`` call does not pay
    the ~164ms cold-probe cost (D-Bus / Keychain / Credential
    Manager round-trip on Linux / macOS / Windows respectively).

    The probe is idempotent: ``credential_store.is_keyring_available``
    caches its result at module level
    (``credential_store._keyring_available_cache``), so subsequent
    calls — including the first ``save`` — read the cached
    value in O(1). Calling more than once is a no-op after the
    first call (the ``config`` module's ``_warmup_called`` flag
    records the first invocation; tests assert on it to verify the
    warmup was wired by the caller). The flag lives in the
    ``config`` module globals and is mutated here via the
    ``voice_typer.server.config`` namespace so the update lands
    where tests read it back.

    The probe is wrapped in ``credential_store.is_keyring_available``'s
    own broad ``except Exception`` (which catches D-Bus connection
    errors, missing pyobjc / pywin32, etc.) — this function does
    NOT add its own try/except so a genuine import error in
    ``credential_store`` surfaces at the call site rather than
    being silently swallowed. The ``_warmup_called`` flag is set to
    True even if the probe itself returns False (keyring
    unavailable) — the WARMUP happened; the unavailability
    is the cached result, not a warmup failure.
    """
    import voice_typer.server.config as _cfg

    if _cfg._warmup_called:
        # Idempotent: a prior call already populated the
        # ``credential_store._keyring_available_cache``. Skip the
        # re-probe (which would be a no-op anyway thanks to the
        # cache, but the flag check avoids the function-call
        # overhead and the global-statement side effect).
        return
    from voice_typer.server import credential_store

    # Touch the probe — the result is cached inside
    # ``credential_store`` (``_keyring_available_cache``) for the
    # process lifetime (positive) or until the re-probe interval
    # (negative). The return value is intentionally ignored here:
    # the caller does not need to know whether keyring is
    # available; the cache is what matters.
    credential_store.is_keyring_available()
    _cfg._warmup_called = True
