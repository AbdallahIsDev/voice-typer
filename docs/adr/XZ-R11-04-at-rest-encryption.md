# ADR: At-Rest Encryption for User Data (Design-Gated)

> **Status**: Implemented (application-layer AES-256-GCM, per-row flag
> design). The canonical cipher module is
> `voice_typer/server/_text_crypto.py`
> (`AESGCM`, 32-byte DEK, 96-bit random nonce, 128-bit tag); the DEK is
> generated/stored/loaded by `voice_typer/server/credential_store/_dek.py`
> in the OS keyring under the existing `KEYRING_SERVICE_NAME` service
> with the reserved `DATA_ENCRYPTION_KEY_USERNAME`
> (`credential_store/_schema.py`). Ciphertext is stored IN the existing
> `transcriptions.text` TEXT column as `"enc:v1:" + base64(nonce ||
> ciphertext || tag)` with a per-row `text_is_encrypted` flag — NOT the
> `text_enc BLOB` + column-swap sketch in §5 below (the flag design was
> amended per review: reversible at every step, no column swap, schema
> migration v4 is DDL-only). Write-side encryption covers all three
> INSERT paths (batched multi-row, per-row fallback, `restore()`); read
> seams decrypt in `history_db_internals/search.py`; pre-existing
> plaintext rows are encrypted by a bounded background backfill on the
> writer thread. FTS5 stays plaintext-tokenized via guarded triggers
> (see §6 and the "Implementation notes" section at the end of this
> document). When the keyring is unavailable the behavior is
> byte-identical to the pre-encryption plaintext mode; there is NO
> on-disk DEK fallback (§9.3).
>
> **Historical correction**: an earlier revision of this header claimed
> a `_text_encryption.py` Fernet module with "read-side live" wiring.
> That module never existed in the tree (verified against git history
> during the 2026-08-25 implementation); the claim was wrong and this
> header now describes the actually-shipped AES-256-GCM design of §4.
>
> **Date**: 2026-11 (design gate) / 2026-08-25 (implementation).
>
> **Owner**: server-side crypto / privacy track.
>
> **Constraints honored**: C-DATA-1 (no unsolicited network — this design
> introduces no network calls; the DEK is stored in the OS keychain, which
> is a local IPC), C-STYLE-1 (no task-ID references inside the document
> body; the filename uses the task ID only as an external design-doc
> reference tag, sanctioned by the task brief).

## 1. Context

Voice Typer persists user data on disk under the platform config dir
(`<config_dir>/`):

| File / object | Contents | Current protection | Personal data? |
|---|---|---|---|
| `history.db` (SQLite, WAL) — table `transcriptions` column `text` | dictated text | POSIX `0o600` + `secure_delete=ON` + WAL truncate on close. **Plaintext on Windows** (chmod is a no-op for non-admin). | **Yes — primary PII** |
| `history.db` FTS5 shadow tables (`transcriptions_fts_data`, `_idx`, `_content`, `_config`, `_docsize`) | tokenized dictated text | same file perms as `history.db` | Yes — derived PII |
| `history.db` metadata columns (`timestamp`, `model`, `device`, `language`, `favorite`) | recording metadata | same | No (operational) |
| `config.json` | hotkey, model selection, cloud_api_url, `keyring://` reference tokens | POSIX `0o600` via `_secure_atomic_write`; reference tokens instead of secrets when keyring available | No (refs only) |
| OS keychain entries (`com.voicetyper.keyring` service) | cloud provider API keys (OpenAI / Groq / Deepgram / cloud / llm) | already encrypted at rest by the OS keychain wrapper (DPAPI / Keychain / SecretService). Managed by `voice_typer/server/credential_store/`. See [`docs/security/credential-store.md`](../security/credential-store.md). | Yes (already protected) |
| `vocabulary.json`, `templates.json` | user-defined phrases / templates (may contain personal snippets) | `0o600` via `PersistedJSON`; single-slot `.bak`; corrupt-quarantine | Secondary PII |
| `crash_diagnostics/*.txt` and `.zip` exports | stack traces; **may include last-N dictated transcriptions** (see `diagnostics_export.py` lines 416–420) | `0o600`; retention sweep | Secondary PII |
| Audio recordings | **Not persisted to disk.** Audio flows through the in-memory ring buffer in `recording/audio_pipeline.py` and is discarded after transcription. No file is written. | n/a | n/a |
| Logs (`logs/*.log`) | redacted by `_redact_text` / PII scrubber; rotate | `0o600` | Low (already scrubbed) |

The dictated-text column (`transcriptions.text`) is the **primary target**
for at-rest encryption. The existing threat model in
`docs/privacy/encryption-at-rest.md` already enumerates three roadmap
options:

- **Option A — SQLCipher (full-DB encryption).** Strongest, but adds a
  native C dependency (`pysqlcipher3` requires the SQLCipher C library)
  and complicates the FTS5 story (FTS5 + SQLCipher has historical
  interactions).
- **Option B — Application-layer encryption of the `text` column only.**
  Uses `cryptography` (pure-Python wheels with OpenSSL). Smaller blast
  radius. FTS5 index handling requires a decision (see §6).
- **Option C — Document-only (current state).** Accept residual risks.

This ADR picks **Option B** with concrete cipher, key source, migration,
cross-platform behavior, performance, key-rotation, and fallback
decisions — concrete enough that an engineer can implement from it
without re-deriving the analysis.

## 2. Decision (summary)

Encrypt the `transcriptions.text` column at the Python application layer
using **AES-256-GCM** (authenticated encryption), with the data-encryption
key (DEK) sourced from the **existing OS keychain integration** in
`voice_typer/server/credential_store/` (same `keyring` library, same
`KEYRING_SERVICE_NAME = "com.voicetyper.keyring"` service, same plaintext
fallback policy). Extend `secure_file_io.py` with a small `EncryptedColumn`
helper so the same atomic-write + TOCTOU-safe-read + 0o600-perms
guarantees apply to the DEK material.

Credentials (API keys) are **already** encrypted at rest via the OS
keychain (RW-01) — that path is unchanged. Settings (`config.json`),
vocabulary, templates, and crash archives keep their current
`0o600`-perms-based protection (the file-level threat is documented; a
future ADR may extend the DEK to wrap these files, but this is out of
scope here).

The FTS5 index will **not** be encrypted at the SQLite layer (tokenized
terms remain in plaintext shadow tables). This is an explicit tradeoff
documented in §6 — full-text search is preserved, with the residual risk
that an attacker with disk access can extract tokenized terms but not
the original continuous dictated text.

## 3. Threat model

Inherits the threat actor table from
[`docs/privacy/encryption-at-rest.md`](../privacy/encryption-at-rest.md)
and adds the at-rest-encryption mitigation column:

| Actor | Capability | Mitigated by (current) | Mitigated by (this design) | Residual |
|---|---|---|---|---|
| **Offline attacker with disk access** (stolen laptop, disk image, disk salvage, cloud-synced `~/.config`) | Read `history.db` directly; recover dictated text from free pages, WAL, journal | POSIX `0o600` + `secure_delete=ON` + WAL truncate on close; **no Windows protection** | AES-256-GCM on `text` column. Without DEK (keychain-encrypted), the column is opaque ciphertext. | None on POSIX+keychain; None on Windows+keychain. Residual when keychain unavailable — see §9. |
| **Same-user process** (another app running as the OS user) | `open("history.db")` and read | POSIX `0o600` blocks other users only; **any same-user process can still read**. On Windows, default ACLs allow same-user read. | Ciphertext requires DEK. DEK lives in keychain; same-user process *can* call `keyring.get_password("com.voicetyper.keyring", "__data_encryption_key__")` and recover DEK. | **Partial mitigation only** — same-user malware that knows to query the keychain still wins. Documented in §10. |
| **Root / admin** | Read everything regardless of perms | None at app layer | DEK is recoverable by root (keychain grants access to root on most platforms). | None. Filesystem-level encryption (FileVault / BitLocker / LUKS) remains the user's responsibility. |
| **Malware with same-user privileges, while app is running** | Read `history.db` + read DEK from keychain | n/a | DEK is cached in process memory after first load. A memory dump captures DEK + plaintext-decrypted rows in the read cache. | Out of scope (same threat model as credential store — see `docs/security/credential-store.md` "What RW-01 does NOT protect against"). |
| **Forensic disk recovery after GDPR delete** | Recover deleted plaintext from free pages / WAL / journal | `secure_delete=ON`; GDPR delete unlinks `history.db*` and `crash_diagnostics/` | After encryption is enabled, deleted rows are ciphertext; even if recovered, they need DEK. | Strengthened. |
| **Backup-tool exposure** (Time Machine, OneDrive, etc.) | Backs up plaintext `history.db` | n/a | Backed-up `history.db` is ciphertext. **However**, if the backup also captures the OS keychain (Time Machine does for macOS Keychain), the DEK travels with the backup. | Strengthened against naive backup; unchanged against keychain-inclusive backups. |
| **Cold-boot / memory dump** | Read DEK + plaintext cache from RAM | n/a | n/a | Out of scope (physical access). |

**Threat-model scope decision**: this design targets the **offline
attacker with disk access** and the **forensic-after-GDPR-delete**
attacker. It does **not** materially improve security against
**same-user malware** (which can call the keychain) or **root/admin**.
That is the same scoping as the existing credential store — no
regression, no false promise.

## 4. Proposed cryptography

### 4.1 Cipher

**AES-256-GCM** (Galois/Counter Mode) — authenticated encryption with
associated data (AEAD).

- 256-bit key (32 raw bytes).
- 96-bit nonce (12 bytes) — random per encryption (NIST SP 800-38D
  permits random nonces with AES-GCM up to ~2³² invocations per key;
  Voice Typer will not approach this within a human lifetime).
- 128-bit authentication tag (16 bytes) — appended to the ciphertext by
  the `cryptography` library.
- Chosen over `cryptography.fernet.Fernet` (AES-128-CBC + HMAC-SHA256):
  Fernet is 128-bit; this design standardizes on 256-bit AES for
  consistency with the broader industry trend and the eventual
  Option A (SQLCipher) upgrade path (SQLCipher defaults to AES-256-CBC).
- Chosen over AES-256-CBC + HMAC: GCM is one-pass AEAD (no separate
  HMAC step), faster on CPUs with AES-NI / ARMv8 crypto extensions, and
  NIST-approved for TLS 1.3.

### 4.2 Library

Use the [`cryptography`](https://cryptography.io) library, specifically
`cryptography.hazmat.primitives.ciphers.aead.AESGCM`. The library is
already a transitive dependency (the `keyring` library pulls it in for
the SecretService backend on Linux), but **`cryptography>=42.0` MUST be
promoted to an explicit top-level dependency** in `pyproject.toml` so
that Windows and macOS installs (where `keyring` uses the native
Credential Manager / Keychain without `cryptography`) get it too. The
library ships manylinux / macOS / Windows wheels with bundled OpenSSL —
no native compilation required.

API sketch (for the implementer):

```python
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
import os


def encrypt_text(plaintext: str, dek: bytes) -> bytes:
    nonce = os.urandom(12)  # 96-bit nonce, random per call
    aes = AESGCM(dek)  # dek is 32 bytes (AES-256)
    ct = aes.encrypt(nonce, plaintext.encode("utf-8"), associated_data=None)
    # ct == ciphertext || tag (16 bytes), as returned by AESGCM.encrypt
    return b"v1" + nonce + ct  # see §4.4 for on-disk format


def decrypt_text(blob: bytes, dek: bytes) -> str:
    version, nonce, ct = _split_blob(blob)  # see §4.4
    if version != b"v1":
        raise ValueError(f"unsupported ciphertext version: {version!r}")
    aes = AESGCM(dek)
    return aes.decrypt(nonce, ct, associated_data=None).decode("utf-8")
```

`AESGCM.decrypt` raises `cryptography.exceptions.InvalidTag` if the tag
does not verify — treat as a corruption event (route through the
existing `history_db_internals/recovery.py` corrupt-DB quarantine path).

### 4.3 Key source — DEK in the OS keychain

The DEK is a 32-byte random value generated once via `os.urandom(32)`
on first launch (after keychain availability is confirmed) and stored in
the OS keychain under the existing service name with a reserved
username:

```
service = KEYRING_SERVICE_NAME   # "com.voicetyper.keyring" — same as API keys
username = "__data_encryption_key__"   # reserved; never used for a cloud provider
secret = base64(dek)              # keyring stores strings; encode bytes as b64
```

The DEK is wrapped by the OS keychain's own at-rest encryption:

| Platform | DEK is wrapped by | Keychain entry visible to |
|---|---|---|
| **Windows** | DPAPI (CryptProtectData, user scope) | the same OS user only; recoverable by root/admin via DPAPI master-key backup |
| **macOS** | Keychain (AES-128, key derived from user's login password) | the same OS user after Keychain unlock |
| **Linux** | libsecret / gnome-keyring (encrypted with the keyring's master password, often unlocked at login) | the same OS user after keyring unlock |

This reuses the **existing** `voice_typer/server/credential_store/`
infrastructure (constants, keyring I/O timeout isolation, availability
probe, plaintext fallback). No new keychain code path is needed — only
a new reserved username plus a small loader / generator helper.

### 4.4 On-disk format (ciphertext blob)

```
+---------+----------+---------------------+-------------------+
| version | nonce    | ciphertext          | GCM tag (16 B)    |
| 2 bytes | 12 bytes | len(plaintext)      | (appended by      |
| "v1"    |          |                     |  AESGCM.encrypt)  |
+---------+----------+---------------------+-------------------+
```

- `version` (2 ASCII bytes, `"v1"`) — leaves room for a future
  cipher-suite upgrade without a schema migration. The decryptor
  dispatches on this byte; unknown versions raise
  `ValueError("unsupported ciphertext version")` and the row is routed
  to corrupt-DB recovery.
- `nonce` (12 bytes) — random per encryption. Never reused with the
  same key.
- `ciphertext` — same length as plaintext (GCM is a stream cipher).
- `tag` (16 bytes) — appended to the ciphertext by `AESGCM.encrypt`.

Storage in SQLite (**as implemented**): the blob is stored as TEXT in
the EXISTING `transcriptions.text` column, prefixed `"enc:v1:"` with
the body base64-encoded, and a per-row `text_is_encrypted` flag marks
which rows are ciphertext (the `text_enc BLOB` column + swap below was
the original sketch; see §5.1's amendment and §16).

### 4.5 Module layout (proposed)

Extend existing modules — do NOT introduce a parallel crypto subsystem:

```
voice_typer/server/credential_store/
  __init__.py            # unchanged exports + new: load_dek / store_dek
  _constants.py          # add: DATA_ENCRYPTION_KEY_USERNAME = "__data_encryption_key__"
  _dek.py                # NEW: load_dek() -> bytes | None
                         #      store_dek(dek: bytes) -> bool
                         #      rotate_dek() -> bytes (new DEK; old kept as prev)
                         #      load_dek_prev() -> bytes | None (for incremental rotation)
                         #      generate_dek() -> bytes (os.urandom(32))

voice_typer/server/secure_file_io.py
  EncryptedColumn        # NEW: small wrapper around AESGCM with the
                         # §4.4 blob format. Reuses _secure_atomic_write
                         # when persisting the (rare) re-keyed DEK to
                         # disk in the plaintext-fallback case (§9).
                         # No file I/O for the happy path — the DEK is
                         # in the keychain, not on disk.

voice_typer/server/history_db_internals/
  crud.py                # encrypt text in add_transcription before INSERT;
                         # decrypt in get_recent / search / get_transcription_text
                         # / get_favorites. Batching preserved.
  schema.py              # schema v4 migration: ADD COLUMN text_enc BLOB;
                         # backfill encrypts all rows (see §5).
```

## 5. Migration plan

### 5.1 Schema migration (v3 → v4)

> **Amended (as implemented)**: the shipped migration does NOT add a
> `text_enc BLOB` column and does NOT swap columns. It adds
> `text_is_encrypted INTEGER DEFAULT 0` to the existing `text` column's
> table and replaces the three FTS5 triggers with encryption-guarded
> variants (DDL-only, single transaction, `schema.py:_MIGRATION_V4`,
> `_CURRENT_SCHEMA_VERSION = 4`). Ciphertext lives in the original
> `text` column behind the flag — reversible at every step, and the
> pre-migration backup path (`history.db.pre-migration-v3.bak`) still
> applies. See "Implementation notes" at the end of this document for
> the full rationale and the trigger-guard semantics.

The migration is **additive + backfill + column swap**, run inside one
explicit `BEGIN; … COMMIT;` (matching the pattern in
`history_db_internals/schema.py:_MIGRATION_V3`):

```sql
-- _MIGRATION_V4 (sketch; final SQL lives in schema.py)
BEGIN;

-- 1. Add the new ciphertext column alongside the old plaintext column.
ALTER TABLE transcriptions ADD COLUMN text_enc BLOB;

-- 2. Backfill: encrypt every existing row's text.
--    Done in Python (NOT in SQL) — see §5.2.
--    Python loops over rows in batches of 100, calls
--    EncryptedColumn.encrypt(text) for each, runs
--    `UPDATE transcriptions SET text_enc = ? WHERE id = ?`.

-- 3. Verify backfill: assert every row has non-NULL text_enc.
--    If any row is missing, ROLLBACK and abort (the schema version
--    is NOT bumped — next launch retries).

-- 4. Swap columns:
--    a. SQLite < 3.35 cannot DROP COLUMN. Use the table-rebuild pattern:
--       CREATE TABLE transcriptions_new (id, text_enc BLOB NOT NULL,
--         timestamp, model, device, language, favorite);
--       INSERT INTO transcriptions_new SELECT id, text_enc, timestamp,
--         model, device, language, favorite FROM transcriptions;
--       DROP TABLE transcriptions;
--       ALTER TABLE transcriptions_new RENAME TO transcriptions;
--       Recreate indexes (idx_favorite, idx_timestamp).
--    b. SQLite >= 3.35 (Voice Typer ships Python 3.10+ which bundles
--       SQLite >= 3.35 on all platforms since 2021): use the simpler
--       `ALTER TABLE transcriptions DROP COLUMN text;`. Detect at
--       runtime via `SELECT sqlite_version()` and pick the path.

-- 5. Rebuild FTS5 (the triggers fire on the rebuilt table).
INSERT INTO transcriptions_fts(transcriptions_fts, rowid, text)
  VALUES ('delete', ...);  -- bulk delete via 'rebuild' command
INSERT INTO transcriptions_fts(rowid, text) SELECT id, ??? FROM transcriptions;
--    ??? — see §6 (FTS5 receives PLAINTEXT during rebuild, since
--    tokenization happens BEFORE storage and the FTS shadow tables
--    are out-of-scope for at-rest encryption).

-- 6. Bump schema version.
INSERT OR REPLACE INTO schema_meta(key, value) VALUES ('version', '4');

COMMIT;
```

### 5.2 Backfill orchestration

The backfill runs in the **existing writer thread** (IMPL-A: there is
exactly one write-capable connection, owned by `HistoryDBWriter`). It
must NOT block app startup. Strategy:

1. On launch, after `init_schema` runs and detects `schema_meta.version
   < 4`, kick off the backfill as a low-priority background task on the
   writer thread (the writer thread already drains a `queue.Queue` —
   enqueue a `_BackfillEncryption` work item that yields control after
   every 100-row batch so it doesn't starve foreground writes).
2. While backfill is in progress, new `add_transcription` calls write
   BOTH the plaintext `text` column AND the ciphertext `text_enc`
   column (so the backfill can catch up without missing rows). This
   requires the `text` column to still exist during the transition.
3. Once backfill completes (verified by a row-count scan: zero rows
   with `text_enc IS NULL`), the column-swap step (§5.1 step 4) runs
   in a single transaction.
4. Progress is surfaced via the existing `_init_error` /
   `health_check()` mechanism so the renderer can show "Encrypting
   existing history (45% complete)".

If the user quits mid-backfill: the next launch resumes. Already-
backfilled rows are idempotent (the UPDATE is a no-op when text_enc is
already populated). The schema version is only bumped to 4 once the
column swap succeeds.

### 5.3 Rollback / abort

If the backfill fails irrecoverably (e.g. DEK unavailable mid-backfill
because the user wiped their keychain):

1. Schema version stays at 3.
2. `text` column is preserved (column swap did not run).
3. `text_enc` column remains populated for the rows that were processed.
4. The next launch sees version 3 + a partial `text_enc` column. It
   re-generates a DEK (the old one is gone) — but `text_enc` values
   are now undecryptable. The migration logic detects this (decrypt
   throws `InvalidTag`) and **discards the partial `text_enc` column**
   (drops it and re-adds), falling back to the plaintext `text`
   column. A WARNING is logged: "At-rest encryption migration
   aborted: DEK lost, partial ciphertext discarded. History is still
   readable in plaintext."

This is the **only** data-loss scenario in the design, and it loses
zero actual user data (the plaintext `text` column was never touched
until the column swap, which only runs on success).

## 6. FTS5 search strategy

The FTS5 virtual table (`transcriptions_fts`) tokenizes the `text`
column via `tokenize='unicode61 remove_diacritics 2'` and stores
tokenized terms in shadow tables (`transcriptions_fts_data`,
`transcriptions_fts_idx`, `transcriptions_fts_content`,
`transcriptions_fts_docsize`, `transcriptions_fts_config`).

Tokenization happens at INSERT time, BEFORE storage. We **cannot**
encrypt-then-tokenize (the tokenizer would receive ciphertext and
produce useless tokens). Three options:

1. **Encrypt the FTS5 shadow tables at the VFS layer** — this is
   SQLCipher territory (Option A in `docs/privacy/encryption-at-rest.md`).
   Out of scope for this design.
2. **Drop FTS5, fall back to `LIKE %query%` on decrypted text** —
   destroys the O(log n + match) search performance that FTS5 bought
   us (M-61). Rejected.
3. **Keep FTS5 in plaintext, encrypt only the `text` column** —
   accepted. The shadow tables store tokenized terms (case-folded,
   diacritics-stripped, fragmented). An attacker with disk access can
   extract the token list but cannot reconstruct the original
   continuous dictated text — they get a bag-of-words view.

**Decision: option 3.** The FTS5 shadow tables remain in plaintext;
only `transcriptions.text` is encrypted. This is the same tradeoff
documented in `docs/privacy/encryption-at-rest.md` Option B:
"encrypt the text column but keep the FTS index unencrypted (accepting
that search-time FTS exposes fragments)".

For users who require stronger protection, a future config flag
`disable_search: true` may drop the FTS5 virtual table entirely
(falling back to `LIKE` on decrypted text — slow, but no shadow
tables). This is out of scope for v1.

## 7. Performance impact

### 7.1 Per-write cost

- Average dictated transcription: 100–500 bytes UTF-8.
- AES-256-GCM encrypt throughput on modern CPUs (AES-NI): ~2–4 GB/s.
  On ARMv8 with crypto extensions: ~1–2 GB/s.
- Per-row encrypt: ~0.05–0.25 ms (dominated by Python overhead, not
  AES).
- History writes already batch via IMPL-A's `_drain_batchable_inserts`
  (up to 100 rows per INSERT). The encrypt step runs **before** the
  batched INSERT, adding ~5–25 ms per batch of 100 — well under the
  30s write-future timeout.
- No fsync overhead added (the ciphertext is in the same SQLite page
  that would have been fsynced anyway).

### 7.2 Per-read cost

- `get_recent` (default `limit=50`): decrypts 50 rows = ~2.5–12 ms.
  Cached at the reader-cache layer (§7.3) so repeated History-page
  renders are free.
- `search` (FTS5): the FTS5 query is unchanged (operates on plaintext
  shadow tables — no decryption needed for the search itself). Only
  the result rows' `text` is decrypted for projection — same ~2.5–12
  ms for 50 results.
- `get_transcription_text` (full-text view): one decrypt — <0.5 ms.

### 7.3 In-memory cache

Decrypt-on-every-read is wasteful for the History UI's default view.
Add a small LRU cache on the `HistoryDB` instance:

- Key: `("text", rowid)`.
- Value: decrypted plaintext `str`.
- Capacity: **50 entries** (matches the default `get_recent` page
  size; covers one History-page render).
- Eviction policy: LRU.
- Invalidation: on `delete(id)`, `clear_all`, and on schema
  migration. The writer thread does NOT touch the cache (it's
  per-reader-thread, mirroring the existing thread-local read
  connection pattern in `_get_read_conn`).
- Security tradeoff: up to 50 plaintext transcriptions live in
  process memory for the lifetime of the reader thread. A
  memory-dump attacker (out-of-scope per §3) can read them. The same
  is true of the existing in-memory `Config` instance (which holds
  plaintext API keys after `load_secret`). No regression.

### 7.4 Keychain access cost

The DEK is loaded **once per process** (lazily on first
`add_transcription` or `get_recent`). After load, it is cached in a
module-level `_dek_cache: bytes | None` (similar to
`_plaintext_config_cache` in `credential_store/_plaintext.py`). No
per-call keychain hit.

The existing `_run_keyring_call` timeout isolation
(`credential_store/_backend.py`) applies: a wedged D-Bus / Keychain on
first load falls through to the plaintext fallback (§9) within the
5s timeout, and the wedge short-circuit kicks in for subsequent
attempts.

## 8. Cross-platform considerations

The DEK storage reuses the existing `keyring` library, which abstracts
the platform backend:

| Platform | Backend (`keyring.get_keyring()` returns) | DEK wrapped by | User-visible behavior |
|---|---|---|---|
| **Windows 10 / 11** | `WindowsCredentialVaultKeyring` (pywin32) | DPAPI (user scope) | DEK stored in Credential Manager under `Target: com.voicetyper.keyring:__data_encryption_key__`. Survives user logoff. Not readable by other users. Recoverable by an administrator with DPAPI master-key backup. |
| **macOS 11+** | `macOSKeyring` (pyobjc) | Keychain (AES-128, key from login password) | DEK stored in the user's login Keychain under service `com.voicetyper.keyring`, account `__data_encryption_key__`. First access shows a Keychain prompt — user clicks "Always Allow". Survives reboot. Not readable by other users. |
| **Linux (with `gnome-keyring-daemon`)** | `SecretServiceKeyring` (libsecret via dbus-python) | libsecret (encrypted with the keyring master password, often the login password) | DEK stored in the GNOME Keyring. Survives logout. |
| **Linux (headless, no `gnome-keyring-daemon`)** | `fail.Keyring` (detected as unavailable by `_probe_keyring`) | n/a — DEK cannot be stored | Falls through to §9 (plaintext fallback: encryption DISABLED). |

This is **identical** to the existing credential-store cross-platform
matrix (see `docs/security/credential-store.md` §"Architecture"). No
new platform gating is needed — `_probe_keyring` already returns
`available=False` on the headless-Linux case, and `is_keyring_available`
is reused as the encryption-enabled gate.

**Windows ACL note**: the existing
`docs/privacy/encryption-at-rest.md` "Residual risks" section flags
that "Windows ACLs inherit from the parent dir; the app does not set
explicit ACLs" for `history.db` itself. With at-rest encryption
enabled, the residual risk on Windows drops from "any same-user
process can read dictated text" to "any same-user process can read
ciphertext + must call DPAPI to unwrap the DEK". A same-user process
CAN call DPAPI (same user scope), so this is not a hardening against
same-user malware — see §10. It IS a hardening against offline disk
salvage (the DPAPI master key is not on the disk image without the
user's password).

## 9. Fallback behavior when keychain is unavailable

The fallback policy **mirrors** the existing credential-store pattern
(`credential_store/_crud.py:store_secret`): never raise, log
the redacted reason, fall back to plaintext.

Concretely:

1. On launch, `is_keyring_available()` is called. If False:
   - `load_dek()` returns `None`.
   - `EncryptedColumn` enters **passthrough mode**: `encrypt(text)`
     returns `text.encode("utf-8")` (no encryption); `decrypt(blob)`
     returns `blob.decode("utf-8")` (no decryption). The schema
     migration (§5) is **deferred** — `text` column stays plaintext.
   - The renderer's existing `KeyringStatusBadge` (amber "Plaintext"
     state) is extended to show "At-rest encryption: DISABLED
     (keychain unavailable)" alongside the existing API-key warning.
2. If the keychain becomes available mid-session (e.g. user starts
   `gnome-keyring-daemon`), the next launch detects it via the
   existing re-probe interval (`_KEYRING_REPROBE_INTERVAL_S`), loads
   the DEK (generating one if none exists), and runs the §5
   migration.
3. **No DEK-on-disk fallback**: the existing credential store writes
   plaintext API keys to `config.json` with `0o600` perms when the
   keychain is unavailable. We deliberately do **NOT** follow this
   pattern for the DEK, because encrypting user data with a DEK that
   lives in the same `config.json` provides zero security (anyone who
   can read `config.json` can read both the DEK and the ciphertext).
   The fallback is therefore **plaintext user data**, not
   **encrypted-with-plaintext-DEK user data**.

This is the only divergence from the credential-store fallback
pattern, and it is justified: the credential store's plaintext
fallback protects a *small, identifiable* set of secrets (5 API keys)
where file perms are a reasonable mitigation; encrypting dictated text
with an on-disk DEK would create a false sense of security without a
real threat-model improvement.

## 10. Key rotation strategy

### 10.1 Why rotate

- Suspected DEK compromise (e.g. memory dump exfiltration, keychain
  backup leak).
- Periodic rotation as hygiene (every N years).
- Cryptographic agility (migrating from AES-256-GCM to a future
  successor — handled via the `version` byte in §4.4, but a full
  re-encrypt is needed if the cipher changes).

### 10.2 Rotation procedure

Rotation = generate a new DEK, re-encrypt every row with the new DEK.

- **Simple model (v1)**: rotation re-encrypts the entire DB in one
  background migration (same machinery as §5.2). The old DEK is
  retained in the keychain under
  `__data_encryption_key_prev__` until the migration completes, then
  deleted. During the migration, both DEKs are loaded; each row's
  ciphertext version byte (`b"v1"`) is checked — if a row is still
  old-DEK ciphertext (migration in progress), the old DEK is used to
  decrypt; once re-encrypted, the new DEK is used.

- **Incremental model (v2, future)**: add a per-row `dek_version
  INTEGER` column. Rotate 100 rows per launch. Supports arbitrary N
  previous DEKs. Deferred until the simple model is shown to be
  insufficient.

### 10.3 User-initiated rotation

Expose an IPC method `rotate_data_encryption_key()` (in a new
`service/privacy.py` handler, alongside the existing
`delete_all_personal_data`). The handler:

1. Calls `credential_store.rotate_dek()` — generates new DEK, stores as
   current, demotes current to prev.
2. Enqueues a `_ReencryptAll` work item on the HistoryDB writer
   thread.
3. Returns immediately with a "rotation in progress" status; the
   renderer polls `health_check()` for completion.

### 10.4 Auto-rotation

No automatic periodic rotation in v1. The threat model (§3) does not
justify the complexity; same-user malware can read the new DEK just as
easily as the old one. Rotation is purely a hygiene / compromise-
response action.

## 11. Consequences

### 11.1 Easier

- **Offline disk-salvage resistance**: a stolen laptop or disk image
  no longer exposes dictated text without the OS keychain's master
  key. The user's FileVault / BitLocker / LUKS password (or lack
  thereof) is no longer the sole protection.
- **Backup-tool resistance**: naive cloud backups of `history.db`
  capture ciphertext, not dictated text. (Keychain-inclusive backups
  remain a residual risk — §3.)
- **Forensic-after-delete strengthening**: even if `secure_delete=ON`
  misses a page (CoW filesystem, etc.), the leftover bytes are
  ciphertext.
- **Foundation for Option A**: a future migration to SQLCipher (full-
  DB encryption) can reuse the DEK keychain integration unchanged —
  only the cipher dispatch changes.

### 11.2 More difficult

- **New top-level dependency**: `cryptography>=42.0` must be added to
  `pyproject.toml` `[project.dependencies]`. The library is ~5 MB
  (manylinux / macOS / Windows wheels with bundled OpenSSL). Already
  transitively present on Linux via `keyring`'s SecretService extras.
- **Schema migration v3 → v4**: non-trivial backfill (§5.2). Risk of
  partial-migration state on crash. Mitigated by the additive-column
  + verify-then-swap pattern.
- **FTS5 residual risk** (§6): dictated-text fragments remain
  extractable from the FTS5 shadow tables. Documented; acceptable per
  the existing threat model.
- **DEK loss = data loss**: if the user wipes their keychain (e.g.
  resets GNOME Keyring), the DEK is gone and `history.db` becomes
  undecryptable. The migration logic (§5.3) detects this on next
  launch and falls back to plaintext mode (discarding the
  undecryptable ciphertext). This is the same UX as the existing
  credential store: "keychain wiped → re-enter your API keys" becomes
  "keychain wiped → history re-encrypted from plaintext" (only if the
  plaintext column still exists mid-migration) or "keychain wiped →
  history lost" (after column swap). The latter is a real risk; the
  GDPR delete path already accepts that keychain-wipe = data-loss for
  API keys, so this is consistent.
- **Same-user malware NOT mitigated** (§3, §10.4): the design does
  not protect against an attacker running as the OS user with
  knowledge of the keychain. This is the same scoping as the existing
  credential store. Documented in `docs/security/credential-store.md`
  "What RW-01 does NOT protect against".

### 11.3 Risks introduced

- **Performance regression if AES-NI unavailable**: on very old CPUs
  (pre-2010 Intel, pre-ARMv8) AES-256-GCM is ~10× slower (~100 MB/s
  instead of ~2 GB/s). Per-row cost rises to ~5 ms. Acceptable for
  the 100-row batch = 500 ms worst case, still under the 30s write
  timeout. Detection: log a one-time WARNING at startup if
  `cryptography.hazmat.backends.openssl.backend._aes_ni_supported` is
  False.
- **InvalidTag on read**: a single corrupted ciphertext byte raises
  `InvalidTag`. The recovery path routes to
  `history_db_internals/recovery.py:maybe_recover_from_corruption`
  (existing) — but that path assumes SQLite-level corruption, not
  ciphertext corruption. New logic needed: if a single row fails
  decryption, log a WARNING and return `"<decryption failed>"` for
  that row's text (do NOT crash the read). The row is preserved (the
  metadata columns are still readable); the user can delete it
  manually via the existing `delete(id)` IPC.
- **DEK migration races**: if the user manually edits `config.json`
  during the v3→v4 migration (unlikely but possible), the
  `secrets_migrated` flag may be reset. Mitigated by the existing
  cross-process `_acquire_config_lock` (used by `Config.save()` and
  `migrate_secrets_to_keyring`).

## 12. Phased rollout (implementation roadmap)

This design is gated by the task list; implementation is broken into
phases so each can be independently reviewed and rolled back:

| Phase | Scope | Risk | Revert path |
|---|---|---|---|
| **P1** | Add `cryptography>=42.0` to `pyproject.toml`. Add `credential_store/_dek.py` with `load_dek` / `store_dek` / `rotate_dek`. No DB changes. | Low (no DB writes). | Drop the new module; dependency can stay (unused). |
| **P2** | Add `secure_file_io.EncryptedColumn` (encrypt/decrypt + blob format). Unit tests with fixed DEK + known ciphertext vectors. | Low (pure function). | Drop the class. |
| **P3** | Schema v4 migration (§5). Additive column + backfill only (no column swap yet). New rows write BOTH `text` (plaintext) and `text_enc` (ciphertext). Reads still use `text`. | Medium (DB write). | Drop column `text_enc` (no data loss — `text` is still authoritative). |
| **P4** | Flip reads to use `text_enc` (decrypt). Keep `text` as a fallback. | Medium. | Flip reads back to `text`. |
| **P5** | Column swap (§5.1 step 4): drop `text`. | High (irreversible per-row). | Restore from `.bak` (the existing `history_db.corrupt-<ts>` snapshot path captures the pre-swap DB). |
| **P6** | User-initiated key rotation IPC (`rotate_data_encryption_key`). | Medium. | n/a (rotation is opt-in). |

P1–P2 can ship independently of P3–P6 (they add capability without
changing behavior). P3–P5 must ship together as one release (a half-
migrated DB across a release boundary is the riskiest state).

## 13. Validation matrix

Per-OS validation (extends the matrix in
`docs/privacy/encryption-at-rest.md`):

| OS | DEK source | Encryption enabled? | Residual risk | Validation step |
|---|---|---|---|---|
| **Linux (with `gnome-keyring-daemon`)** | SecretService | ✅ Yes | Same-user malware (out-of-scope). | `secret-tool search service com.voicetyper.keyring username __data_encryption_key__` returns the entry; `history.db` row `text_enc` is non-readable (BLOB of `v1` + 12-byte nonce + ciphertext + 16-byte tag). |
| **macOS (Keychain)** | Keychain | ✅ Yes | Same + Time Machine backup of Keychain (residual). | `security find-generic-password -s com.voicetyper.keyring -a __data_encryption_key__` returns the entry. |
| **Windows (Credential Manager)** | DPAPI | ✅ Yes | Same-user malware (out-of-scope). | `cmdkey /list` shows `com.voicetyper.keyring:__data_encryption_key__`. |
| **Linux (headless, no keyring daemon)** | n/a | ❌ No (passthrough) | Same as current state — dictated text in plaintext. | `history.db` row `text` is plaintext; renderer shows amber "encryption disabled" badge. |

## 14. References

- [`docs/privacy/encryption-at-rest.md`](../privacy/encryption-at-rest.md)
  — normative threat model and per-OS validation matrix. This ADR's
  Option B picks one of the three roadmap options enumerated there.
- [`docs/security/credential-store.md`](../security/credential-store.md)
  — the existing keychain integration that this design extends. Same
  `KEYRING_SERVICE_NAME`, same fallback policy, same cross-platform
  matrix.
- [`docs/adr/0001-record-architecture-decisions.md`](0001-record-architecture-decisions.md)
  — ADR format.
- `voice_typer/server/credential_store/__init__.py` — public API surface
  (`load_secret`, `store_secret`, `delete_secret`,
  `migrate_secrets_to_keyring`, `is_keyring_available`,
  `get_keyring_status`). This design adds `load_dek`, `store_dek`,
  `rotate_dek` to the same module.
- `voice_typer/server/credential_store/_constants.py` — `KEYRING_SERVICE_NAME
  = "com.voicetyper.keyring"`. This design adds
  `DATA_ENCRYPTION_KEY_USERNAME = "__data_encryption_key__"` (and
  `__data_encryption_key_prev__` for rotation).
- `voice_typer/server/credential_store/_keyring_io.py` — `_run_keyring_call`
  with 5s timeout + orphan/wedge tracking. Reused unchanged for DEK
  load/store.
- `voice_typer/server/credential_store/_availability.py` —
  `is_keyring_available` + re-probe interval. Reused unchanged as the
  encryption-enabled gate.
- `voice_typer/server/secure_file_io.py` — `_secure_atomic_write`,
  `_secure_read_text` (TOCTOU-safe), `PersistedJSON` (atomic + .bak +
  corrupt-quarantine). This design adds `EncryptedColumn` to the same
  module.
- `voice_typer/server/history_db.py` — IMPL-A single-writer
  architecture. The encrypt step runs in `_writer_loop`; the decrypt
  step runs in `_get_read_conn`'s thread-local read path.
- `voice_typer/server/history_db_internals/schema.py` — `_MIGRATIONS`
  dict; this design adds `_MIGRATION_V4`.
- `voice_typer/server/history_db_internals/crud.py` — `add_transcription`
  / `get_recent` / `search` / `get_transcription_text` call sites for
  encrypt / decrypt.
- `voice_typer/server/diagnostics_export.py` — line 416–420: notes
  that crash-diagnostic archives may include dictated-text snippets.
  Out of scope for v1 (archives are transient, retention-capped, and
  `0o600`-protected); a future ADR may extend the DEK to wrap archive
  files.
- `pyproject.toml` — `keyring>=25.0,<26.0` already a dependency.
  `cryptography>=42.0` must be promoted to top-level (currently
  transitive on Linux only).

## 15. Open questions (deferred to implementation)

1. **Migration concurrency with active dictation**: if the user is
   dictating while the v4 backfill runs, the new-row INSERT writes both
   `text` and `text_enc`. Confirm the writer-thread queue ordering
   guarantees the backfill's UPDATE never races the new-row INSERT
   (it should — both go through the single writer thread).
2. **DEK versioning vs. cipher versioning**: the `version` byte in
   §4.4 covers cipher-suite upgrades. Per-row DEK versioning (for
   incremental rotation) is deferred to v2 — confirm in P6 that the
   simple-model rotation completes in acceptable time for the largest
   expected DB (≈100k rows ≈ 30s on AES-NI hardware).
3. **Crash-archive encryption**: should the DEK also wrap
   `crash_diagnostics/*.zip` exports? The current archive
   includes last-N dictated transcriptions (per
   `diagnostics_export.py` line 416). Out of scope for v1; revisit if
   the threat model expands to include shared diagnostic exports
   (e.g. user emails a zip to support).
4. **Vocabulary / templates encryption**: these store user-defined
   phrases (may contain personal snippets) via `PersistedJSON`. The
   `0o600` perms + single-slot `.bak` is currently deemed sufficient.
   Revisit if a user reports a real-world compromise vector via
   vocabulary backup leak.

## 16. Implementation notes (2026-08-25 — supersedes the sketches above where they differ)

The sections above remain the design gate; this section records what
actually shipped and the deviations, each with its reason.

### 16.1 Module layout (actual)

- `voice_typer/server/credential_store/_schema.py` —
  `DATA_ENCRYPTION_KEY_USERNAME = "__data_encryption_key__"` (the
  reserved keyring username of §4.3).
- `voice_typer/server/credential_store/_dek.py` — `generate_dek` /
  `store_dek` / `load_dek`. Calls `keyring.set_password` /
  `keyring.get_password` DIRECTLY through `_run_keyring_call` (timeout
  isolation + orphan/wedge tracking): `store_secret` rejects providers
  not in `PROVIDER_TO_CONFIG_FIELD` by design, and the DEK must never
  flow through the plaintext-`config.json` fallback (§9.3). Base64
  transport (keyring stores strings); a stored value that does not
  decode to exactly 32 bytes is treated as absent.
- `voice_typer/server/_text_crypto.py` — the ONE canonical crypto
  module (E7): `encrypt_text` / `decrypt_text` / `is_encrypted`, blob
  format `"enc:v1:" + base64(nonce(12) || ciphertext || tag(16))` in
  the existing TEXT column, the process-lifetime DEK cache
  (`resolve_dek` / `get_dek_cached` / `reset_dek_cache`), the
  `"<decryption failed>"` placeholder policy, and a shared
  rate-limited logger. `secure_file_io.EncryptedColumn` from the §4.5
  sketch was NOT built — the DEK lives in the keyring only, so there
  is no file I/O to wrap.
- `voice_typer/server/history_db_internals/schema.py` —
  `_MIGRATION_V4` + `_CURRENT_SCHEMA_VERSION = 4`.
- `voice_typer/server/history_db_internals/writer.py` — encryption on
  both INSERT paths (batched multi-row and per-row fallback).
- `voice_typer/server/history_db_internals/search.py` — flag-aware
  read seams for get_recent / search / get_favorites /
  get_latest_text / get_transcription_text.
- `voice_typer/server/history_db.py` — `restore()` encryption, DEK
  resolution on the writer thread (`_init_encryption`), the bounded
  backfill, and the `encryption_status()` surface
  (`"active"` / `"disabled"` / `"key-unavailable"`).

### 16.2 FTS5 trigger guards (the load-bearing detail)

Rows are ALWAYS inserted with plaintext and flag 0 — the AFTER-INSERT
trigger indexes the plaintext tokens — and then flipped to ciphertext +
flag 1 with an UPDATE in the same transaction. For that to work the
v4 migration recreates all three triggers with WHEN guards:

- `ai_fts`: `WHEN new.text_is_encrypted = 0` — never index ciphertext
  (protects the corruption-recovery replay path too).
- `ad_fts`: `WHEN old.text_is_encrypted = 0` — the FTS5 `'delete'`
  command requires the SAME token stream that was originally indexed;
  issuing it with ciphertext raises `database disk image is malformed`
  (verified in-sandbox, SQLite 3.53). Token removal is skipped for
  encrypted rows; stale rowids are filtered out by the JOIN in every
  search SQL.
- `au_fts`: `WHEN NEW.text_is_encrypted = 0 AND OLD.text_is_encrypted = 0`.
  The stricter form (not merely "flag unchanged") is REQUIRED: a
  favorite-toggle UPDATE on an encrypted row also has an unchanged
  flag, and the unguarded variant demonstrably corrupts the index.
  Real text edits on plaintext rows still re-index normally.

**Residual (documented)**: an FTS5 `'rebuild'` command re-tokenizes
from the content table, so a rebuild that fires while encrypted rows
exist replaces their plaintext tokens with ciphertext tokens — FTS
search then no longer matches those rows (no corruption; the rows
remain readable and decryptable). `'optimize'` (used by the per-row
delete path) does not re-read content and is unaffected. The startup
rebuild is ordered BEFORE backfill encryption, so the common paths are
covered; a decrypt-aware rebuild (re-populating tokens from decrypted
text) is a follow-up that requires touching `retention.py` and the
clear_all/startup paths together.

### 16.3 Key-loss policy (stricter than §9)

§9.3's "re-generates a DEK" fallback is NOT shipped. When encrypted
rows exist and the DEK cannot be loaded (keychain wiped, backend
down): reads of flagged rows return `"<decryption failed>"` (never the
ciphertext, never a crash) plus a rate-limited ERROR; NEW writes stay
plaintext (flag 0) so no further rows depend on the lost key; and the
DEK is NEVER regenerated in this state — a fresh key cannot decrypt
the existing rows and would silently orphan them. A new DEK is
generated only when the keyring is available AND zero encrypted rows
exist. `HistoryDB.encryption_status()` distinguishes
`"key-unavailable"` from first-run `"disabled"`.

### 16.4 Backfill

`HistoryDB._init_encryption` (writer thread, before the ready signal —
the single DEK keyring read is bounded by the 5s keyring-I/O timeout)
enqueues `_encrypt_backfill_step` items: each encrypts up to 100
plaintext rows in one transaction and re-enqueues itself, so foreground
dictation writes are never starved. Idempotent by flag; resumes across
launches; skipped entirely when no DEK is available.

*End of design document.*
