//! PII redaction engine for log output (std-only, no `regex` dep).

// PII redaction ────────────────────────────────────
//
// PII redactor for log output. Ports the Python `PIIRedactionFilter`
// pattern set to Rust using std-only substring scanning (no `regex`
// crate dependency). Covered patterns (checked at each byte position
// in the order listed; first match wins):
//
// Prefix / keyword patterns (Python `_KEY_PATTERNS` + `_FLAG_KEY_PATTERNS`):
// - Bearer <token>           → `Bearer ***`   (Python `_KEY_PATTERNS[0]`)
// - Token <token>            → `Token ***`    (Python `_KEY_PATTERNS[1]`)
// - sk-<token> (8+ chars)    → `sk-***`       (Python `_KEY_PATTERNS[2]`)
// - gsk_<token> (8+ chars)   → `gsk_***`      (Python `_KEY_PATTERNS[3]`)
// keyword=value          → `--keyword=***` (Python `_FLAG_VALUE_PATTERN`, )
// keyword value          → `--keyword ***` (Python `_FLAG_VALUE_PATTERN`, )
// keyword=value            → `keyword=***`   (Python `_BARE_KEY_VALUE_PATTERN`, )
//
// PII patterns (Python `_PATTERNS`):
// - user:pass@host      → `***@host`     (Python `redact_url`)
// - <local>@<dom>.<tld> → `[EMAIL]`      (Python `_PATTERNS[0]`)
// - IBAN                → `[IBAN]`       (Python `_PATTERNS[1]`)
// - Intl phone (+cc…)   → `[PHONE]`      (Python `_PATTERNS[3]`)
// - US phone (3-3-4)    → `[PHONE]`      (Python `_PATTERNS[2]`)
// - SSN (3-2-4)         → `[SSN]`        (Python `_PATTERNS[4]`)
// - Credit card (4-4-4-4) → `[CC]`      (Python `_PATTERNS[5]`)
//
//Catch-all (Python `_KEY_PATTERNS[4]`, ):
// - 20+ char alphanumeric run → `***`   (`\b[A-Za-z0-9_\-]{20,}\b`)
//
// The fast path mirrors Python's `_FAST_TRIGGER` (security.py:63):
// `[@+]|\d{3,}|Bearer|Token|sk-|key=|[A-Za-z0-9_\-]{20,}`
// We check the substrings directly (no regex), a 3+ consecutive
// digit scan for the numeric patterns, and a 20+ char alphanumeric
// run scan for the catch-all. A miss on ALL triggers lets us return
// the input unchanged without the per-byte scan loop.

/// Redact PII patterns from the input string. Returns the input unchanged
/// (but as a newly-allocated `String`) if no trigger character is
/// present. Otherwise returns a new `String` with each matched pattern
/// replaced by its redaction placeholder.
pub(crate) fn redact_pii(input: &str) -> String {
    // Fast path: skip the whole pass if no trigger is present. Mirrors
    // the Python side's `_FAST_TRIGGER` shortcut (security.py:63). Each
    // trigger is a *necessary* condition for at least one downstream
    // pattern:
    // `@`      — email / URL credentials
    // `+`      — international phone (`+<cc>…`)
    // `Bearer` — bearer token
    // `Token`  — token keyword
    // `sk-`    — OpenAI-style key
    // `gsk_`   — Groq-style key
    // `://`    — URL credentials (`https://user:pass@host`)
    // 3+ consecutive ASCII digits — US phone, SSN, CC, IBAN (BBAN
    // portion always contains 3+ consecutive digits)
    // `key=`   — bare `key=value` flag form (Python `_FAST_TRIGGER`);
    // also a substring of `--key=`, `--api_key=`, `--api-key=`, and
    // any `--<keyword>=` flag whose keyword ends in `key`. Case-
    //sensitive (mirrors Python).
    // 20+ char alphanumeric run — the generic 20+ char bare-token
    // pattern (`\b[A-Za-z0-9_\-]{20,}\b`, Python `_KEY_PATTERNS[4]`).
    //
    if !has_any_fast_trigger(input) {
        return input.to_string();
    }

    let mut out = String::with_capacity(input.len());
    let mut i = 0;
    while i < input.len() {
        let rest = &input[i..];

        // 1. `Bearer <token>` — token runs until a char that's NOT in
        // the Python `_KEY_PATTERNS[0]` charset `[A-Za-z0-9_\-\.=]`.
        // Pre-fix the token ran until whitespace, which consumed
        // trailing punctuation (e.g. the comma in `Bearer abc123,`)
        // and broke `test_redact_pii_multiple_patterns_in_one_line`.
        if let Some(stripped) = rest.strip_prefix("Bearer ") {
            let token_len = stripped
                .find(|c: char| !is_api_token_char(c))
                .unwrap_or(stripped.len());
            out.push_str("Bearer ***");
            i += "Bearer ".len() + token_len;
            continue;
        }
        // 2. `Token <token>` — same charset as Bearer.
        if let Some(stripped) = rest.strip_prefix("Token ") {
            let token_len = stripped
                .find(|c: char| !is_api_token_char(c))
                .unwrap_or(stripped.len());
            out.push_str("Token ***");
            i += "Token ".len() + token_len;
            continue;
        }
        // 3. `sk-<token>` — token runs until non-alphanumeric / non
        // dash / non underscore (the typical API-key charset).
        if let Some(stripped) = rest.strip_prefix("sk-") {
            let token_len = stripped
                .find(|c: char| !c.is_ascii_alphanumeric() && c != '-' && c != '_')
                .unwrap_or(stripped.len());
            if token_len >= 8 {
                // Only redact if the token looks like a real key
                // (>= 8 chars after the prefix); otherwise leave it
                // alone (e.g. `sk-1234` in a model name).
                out.push_str("sk-***");
                i += "sk-".len() + token_len;
                continue;
            }
        }
        // 4. `gsk_<token>` — Groq-style API key. Same charset and
        // length threshold as `sk-` (8+ chars after the prefix).
        if let Some(stripped) = rest.strip_prefix("gsk_") {
            let token_len = stripped
                .find(|c: char| !c.is_ascii_alphanumeric() && c != '-' && c != '_')
                .unwrap_or(stripped.len());
            if token_len >= 8 {
                out.push_str("gsk_***");
                i += "gsk_".len() + token_len;
                continue;
            }
        }

        //5. Flag-form and bare-keyword secret patterns (). Mirrors
        // Python's `_FLAG_VALUE_PATTERN` (Pattern A: `--keyword=value`
        // or `--keyword value`) and `_BARE_KEY_VALUE_PATTERN`
        // (Pattern B: `keyword=value`). Checked AFTER the prefix
        // patterns (Bearer/Token/sk-/gsk_) so those specific prefixes
        // win, but BEFORE the PII patterns (email/IBAN/phone/SSN/CC)
        // so a secret-bearing flag value containing `@` or digits
        // (e.g. `token=alice@example.com`) is redacted as a secret
        // (`token=***`) rather than as PII (`token=[EMAIL]`).
        //
        // See `SECRET_KEYWORDS` below for the keyword list and
        // `try_match_flag_or_bare_key` for the matching logic.
        if let Some((total_len, prefix_len)) = try_match_flag_or_bare_key(rest, input, i) {
            out.push_str(&rest[..prefix_len]);
            out.push_str("***");
            i += total_len;
            continue;
        }

        // 6. `user:pass@host` — strip everything up to and including
        // the `@` IF the prefix contains a `:` (the URL-credential
        // marker). The host part is preserved.
        if rest.contains('@') {
            if let Some(at_pos) = rest.find('@') {
                let prefix = &rest[..at_pos];
                if prefix.contains(':') && !prefix.contains(' ') && !prefix.is_empty() {
                    out.push_str("***@");
                    i += at_pos + 1;
                    continue;
                }
                // 7. Basic email: `<name>@<domain>.<tld>`. We require
                // a `.` in the domain part (after the `@`) to avoid
                // false-positives on `user@host` (no TLD).
                let after_at = &rest[at_pos + 1..];
                let domain_end = after_at
                    .find(|c: char| c.is_whitespace() || c == ',' || c == ';')
                    .unwrap_or(after_at.len());
                let domain = &after_at[..domain_end];
                if domain.contains('.') && !domain.starts_with('.') {
                    let local_valid = !prefix.is_empty()
                        && prefix.chars().all(|c| {
                            c.is_alphanumeric() || c == '.' || c == '-' || c == '_' || c == '+'
                        });
                    if local_valid {
                        out.push_str("[EMAIL]");
                        i += at_pos + 1 + domain_end;
                        continue;
                    }
                }
            }
        }

        // 8. IBAN: 2 uppercase ASCII letters + 2 digits + 10-30 BBAN
        // chars (uppercase letters or digits). MUST be checked
        // before phone/SSN/CC so the digit portion of an IBAN
        // (e.g. `GB82WEST12345698765432`) isn't partially matched
        // as a phone number. Mirrors Python `_PATTERNS[1]`:
        // `\b[A-Z]{2}\d{2}[A-Z0-9]{10,30}\b`.
        if let Some(iban_len) = try_match_iban(rest, input, i) {
            out.push_str("[IBAN]");
            i += iban_len;
            continue;
        }

        // 9. International phone: `+` followed by country code (1-3
        // digits) and subscriber number. Mirrors Python
        // `_PATTERNS[3]`: `\+\d{1,3}[\s-]?\(?\d{1,4}\)?[\s-]?\d{3,4}[\s-]?\d{3,4}\b`.
        // Checked before US phone so `+1 (415) 555-2671` matches
        // the international pattern (not the US pattern on the
        // `415 555 2671` tail).
        if rest.starts_with('+') {
            if let Some(phone_len) = try_match_intl_phone(rest, input, i) {
                out.push_str("[PHONE]");
                i += phone_len;
                continue;
            }
        }

        // 10. US phone: 3-3-4 digits with optional `-` or `.`
        // separators. Mirrors Python `_PATTERNS[2]`:
        // `\b\d{3}[-.]?\d{3}[-.]?\d{4}\b`.
        if let Some(phone_len) = try_match_us_phone(rest, input, i) {
            out.push_str("[PHONE]");
            i += phone_len;
            continue;
        }

        // 11. SSN: 3-2-4 digits with `-` separators (the canonical
        // `123-45-6789` form). Mirrors Python `_PATTERNS[4]`:
        // `\b\d{3}-\d{2}-\d{4}\b`. The dashes are REQUIRED (not
        // optional) so a 9-digit run like `123456789` is NOT
        // matched as an SSN (matches Python behaviour).
        if let Some(ssn_len) = try_match_ssn(rest, input, i) {
            out.push_str("[SSN]");
            i += ssn_len;
            continue;
        }

        // 12. Credit card: 4-4-4-4 digits with optional `-` or space
        // separators. Mirrors Python `_PATTERNS[5]`:
        // `\b\d{4}[-\s]?\d{4}[-\s]?\d{4}[-\s]?\d{4}\b`.
        if let Some(cc_len) = try_match_credit_card(rest, input, i) {
            out.push_str("[CC]");
            i += cc_len;
            continue;
        }

        //13. 20+ char alphanumeric catch-all (). Mirrors Python's
        // `_KEY_PATTERNS[4]`: `\b[A-Za-z0-9_\-]{20,}\b`. Catches
        // bare GitLab/GitHub/Slack PATs with no prefix. Checked LAST
        // (after all PII patterns) so an IBAN like
        // `GB82WEST12345698765432` (20 chars) is redacted as `[IBAN]`
        // rather than `***`.
        if let Some(run_len) = try_match_long_alphanumeric_run(rest, input, i) {
            out.push_str("***");
            i += run_len;
            continue;
        }

        // No pattern matched at this position — copy the char and
        //advance by its UTF-8 length. : use `if let Some` instead
        // of `unwrap()` — defense-in-depth. The loop invariant
        // (`i < input.len()`) guarantees `rest` is non-empty, so the
        // `else { break; }` branch is unreachable today. But a future
        // refactor that changes the loop bound (e.g. off-by-one) would
        // turn `unwrap()` into a panic inside the logger — and logger
        // panics are self-reinforcing (the panic hook calls `log::error!`
        // which calls `redact_pii` which panics again → abort). The
        // `if let Some` form degrades gracefully instead.
        if let Some(ch) = rest.chars().next() {
            out.push(ch);
            i += ch.len_utf8();
        } else {
            break;
        }
    }
    out
}

/// Predicate matching the Python `_KEY_PATTERNS` charset
/// `[A-Za-z0-9_\-\.=]` used by the `Bearer` / `Token` prefix patterns.
/// Used by `redact_pii` to find the end of a bearer/token value without
/// consuming trailing punctuation (commas, semicolons, quotes) that
/// Python's regex would leave alone.
fn is_api_token_char(c: char) -> bool {
    c.is_ascii_alphanumeric() || c == '_' || c == '-' || c == '.' || c == '='
}

//flag-form + bare-keyword + 20+ char catch-all helpers ────────

/// Secret-bearing keywords for the flag-form and bare-keyword patterns.
/// Mirrors Python's `_SECRET_KEYWORDS` in `voice_typer/server/_secrets.py:88-115`,
//plus `bearer` and `credential` ( task-specified additions not in
/// Python's list but unambiguously secret-bearing).
///
/// **Order matters**: most-specific first, `key` last — so `api_key=`
/// wins over `key=` when both could match at the same position. Python's
/// regex alternation is leftmost-first (tries each alternative in order),
/// so we iterate in the same order. The `\b` word boundary (checked in
/// `try_match_flag_or_bare_key` for the bare-keyword form) prevents
/// matching inside larger words like `monkey=` or `hotkey=`.
const SECRET_KEYWORDS: &[&str] = &[
    "token",
    "apikey",
    "api_key",
    "api-key",
    "secret",
    "password",
    "passwd",
    "pwd",
    "auth",
    "authorization",
    "authentication",
    "access_token",
    "access-token",
    "refreshtoken",
    "refresh_token",
    "refresh-token",
    "client_secret",
    "client-secret",
    "private_key",
    "private-key",
    //task-specified additions (not in Python's _SECRET_KEYWORDS
    // but unambiguously secret-bearing).
    "bearer",
    "credential",
    // `key` MUST be last — Python orders from most-specific to least-
    // specific so `api_key=` / `access_token=` / etc. win over `key=`.
    "key",
];

/// Single-pass fast-path trigger scan. Returns `true` iff at least one
/// of the trigger conditions below holds — the same set the previous
/// per-pattern scans checked, now evaluated in ONE byte loop instead
/// of 10 separate passes (8 substring scans + 2 dedicated run scans).
/// A miss on every trigger lets [`redact_pii`] return the input
/// unchanged without the per-byte pattern loop.
///
/// Trigger conditions (each is a *necessary* condition for at least one
/// downstream pattern):
/// - `@`      — email / URL credentials
/// - `+`      — international phone (`+<cc>…`)
/// - `Bearer` / `Token` — keyword prefixes
/// - `sk-`    — OpenAI-style key
/// - `gsk_`   — Groq-style key
/// - `://`    — URL credentials (`https://user:pass@host`)
/// - `key=`   — bare `key=value` flag form (Python `_FAST_TRIGGER`);
///   also a substring of `--key=`, `--api_key=`, `--api-key=`, and any
///   `--<keyword>=` flag whose keyword ends in `key`. Case-sensitive
///   (mirrors Python).
/// - 3+ consecutive ASCII digits — US phone, SSN, CC, IBAN (the BBAN
///   portion always contains 3+ consecutive digits)
/// - 20+ char `[A-Za-z0-9_\-]` run — the generic bare-token catch-all
///   (`\b[A-Za-z0-9_\-]{20,}\b`, Python `_KEY_PATTERNS[4]`)
///
/// Equivalence with the previous separate scans: all substring needles
/// are ASCII, so first-byte dispatch + full compare at that position
/// matches exactly where `str::contains` would (ASCII bytes occur only
/// as themselves in UTF-8); the digit-run and long-run counters track
/// the identical byte predicates the removed dedicated scans used
/// (non-matching bytes — including non-ASCII — reset both runs).
///
/// `pub(crate)` so `platform::logging` can re-export it (cfg(test))
/// to the sibling `logging_tests` module.
pub(crate) fn has_any_fast_trigger(input: &str) -> bool {
    let bytes = input.as_bytes();
    let len = bytes.len();
    let mut digit_run = 0u8;
    let mut long_run = 0u32;
    let mut i = 0;
    while i < len {
        let b = bytes[i];
        // Single-char triggers: `@` (email / URL credentials) and
        // `+` (international phone).
        if b == b'@' || b == b'+' {
            return true;
        }
        // 3+ consecutive ASCII digits (US phone / SSN / CC / IBAN).
        if b.is_ascii_digit() {
            digit_run += 1;
            if digit_run >= 3 {
                return true;
            }
        } else {
            digit_run = 0;
        }
        // 20+ char `[A-Za-z0-9_\-]` run (bare-token catch-all).
        if is_long_run_char(b) {
            long_run += 1;
            if long_run >= 20 {
                return true;
            }
        } else {
            long_run = 0;
        }
        // Multi-byte substring triggers, dispatched on the first byte.
        match b {
            b'B' => {
                if bytes[i..].starts_with(b"Bearer") {
                    return true;
                }
            }
            b'T' => {
                if bytes[i..].starts_with(b"Token") {
                    return true;
                }
            }
            b's' => {
                if bytes[i..].starts_with(b"sk-") {
                    return true;
                }
            }
            b'g' => {
                if bytes[i..].starts_with(b"gsk_") {
                    return true;
                }
            }
            b':' => {
                if bytes[i..].starts_with(b"://") {
                    return true;
                }
            }
            b'k' => {
                if bytes[i..].starts_with(b"key=") {
                    return true;
                }
            }
            _ => {}
        }
        i += 1;
    }
    false
}

/// Predicate for the 20+ char run charset `[A-Za-z0-9_\-]` (includes
/// `-`, unlike `is_python_word_char`).
fn is_long_run_char(b: u8) -> bool {
    b.is_ascii_alphanumeric() || b == b'_' || b == b'-'
}

/// Predicate for Python's word-char set `[A-Za-z0-9_]` (excludes `-`).
/// Used for `\b` word-boundary checks mirroring Python's regex semantics.
fn is_python_word_char(b: u8) -> bool {
    b.is_ascii_alphanumeric() || b == b'_'
}

/// Try to match a flag-form or bare-keyword secret at the start of
/// `rest`. Mirrors Python's `_FLAG_VALUE_PATTERN` (Pattern A:
/// `--keyword=value` or `--keyword value`) and `_BARE_KEY_VALUE_PATTERN`
/// (Pattern B: `keyword=value`) from `voice_typer/server/_secrets.py`.
///
/// Returns `Some((total_len, prefix_len))` on success:
/// - `total_len` — total byte length of the match (to advance the main
///   loop index `i`).
/// - `prefix_len` — byte length of the prefix to preserve in the output
///   (e.g. `--token=`, `--token ` with whitespace, or `token=`). The
///   caller outputs `&rest[..prefix_len]` + `"***"`.
///
/// Matching is case-insensitive (mirrors Python's `(?i)` flag on both
/// patterns). The value (`[^\s=]+` in Python) runs until the next
/// whitespace or `=` char and must be at least 1 char (empty values are
/// NOT redacted — `--token=` with nothing after the `=` is left alone).
///
/// Pattern A (flag form) requires `--` prefix; no word boundary is
/// required before `--` (mirrors Python's `_FLAG_VALUE_PATTERN` which
/// has no `\b`). Pattern B (bare-keyword) requires `\b` before the
/// keyword (mirrors Python's `_BARE_KEY_VALUE_PATTERN` which starts
/// with `\b`) so `monkey=abc` does NOT match `key=abc`.
fn try_match_flag_or_bare_key(rest: &str, input: &str, pos: usize) -> Option<(usize, usize)> {
    // Pattern A: `--keyword=value` or `--keyword value`.
    // No `\b` required before `--` (Python's _FLAG_VALUE_PATTERN has none).
    if let Some(after_dashes) = rest.strip_prefix("--") {
        for &kw in SECRET_KEYWORDS {
            // Compare bytes (not str slices) to avoid UTF-8 panic when
            // ``kw.len()`` lands inside a multi-byte char in ``after_dashes``.
            // The redaction engine runs inside the panic hook, so a
            // str-slice panic here would be self-reinforcing.
            if after_dashes.len() >= kw.len()
                && after_dashes.as_bytes()[..kw.len()].eq_ignore_ascii_case(kw.as_bytes())
            {
                let after_kw = &after_dashes[kw.len()..];
                // Delimiter: `=` (equals form) or one-or-more whitespace
                // chars (space form). Mirrors Python's `(?:=|\s+)`.
                if let Some(value_rest) = after_kw.strip_prefix('=') {
                    // `--keyword=value` form.
                    let value_len = value_rest
                        .find(|c: char| c.is_whitespace() || c == '=')
                        .unwrap_or(value_rest.len());
                    if value_len > 0 {
                        let prefix_len = 2 + kw.len() + 1; // `--` + kw + `=`
                        let total = prefix_len + value_len;
                        return Some((total, prefix_len));
                    }
                } else if let Some(first_non_ws) =
                    after_kw.bytes().position(|b| !b.is_ascii_whitespace())
                {
                    // `--keyword value` form. `\s+` requires at least one
                    // whitespace char before the value.
                    if first_non_ws > 0 {
                        let value_rest = &after_kw[first_non_ws..];
                        let value_len = value_rest
                            .find(|c: char| c.is_whitespace() || c == '=')
                            .unwrap_or(value_rest.len());
                        if value_len > 0 {
                            // `--` + kw + whitespace separator
                            let prefix_len = 2 + kw.len() + first_non_ws;
                            let total = prefix_len + value_len;
                            return Some((total, prefix_len));
                        }
                    }
                }
            }
        }
    }
    // Pattern B: `keyword=value` (no `--` prefix). Requires `\b` before
    // the keyword (mirrors Python's `\b` in `_BARE_KEY_VALUE_PATTERN`).
    if !word_boundary_before(input, pos) {
        return None;
    }
    for &kw in SECRET_KEYWORDS {
        // Compare bytes (not str slices) to avoid UTF-8 panic when
        // ``kw.len()`` lands inside a multi-byte char in ``rest``.
        // The redaction engine runs inside the panic hook, so a
        // str-slice panic here would be self-reinforcing
        // (panic → log::error! → redact_pii → panic → abort).
        if rest.len() > kw.len()
            && rest.as_bytes()[..kw.len()].eq_ignore_ascii_case(kw.as_bytes())
            && rest.as_bytes()[kw.len()] == b'='
        {
            let value_rest = &rest[kw.len() + 1..];
            let value_len = value_rest
                .find(|c: char| c.is_whitespace() || c == '=')
                .unwrap_or(value_rest.len());
            if value_len > 0 {
                let prefix_len = kw.len() + 1; // kw + `=`
                let total = prefix_len + value_len;
                return Some((total, prefix_len));
            }
        }
    }
    None
}

/// Try to match a 20+ char alphanumeric run at the start of `rest`.
/// Mirrors Python's `_KEY_PATTERNS[4]`: `\b[A-Za-z0-9_\-]{20,}\b`.
/// Catches bare GitLab/GitHub/Slack PATs with no prefix.
///
/// Returns the match length (in bytes) if the pattern matches, or
/// `None` otherwise. Word boundaries (`\b`) are checked at both ends
/// using Python's exact `\b` semantics: a boundary holds iff the
/// word-ness (`[A-Za-z0-9_]`, excluding `-`) differs between the
/// adjacent chars (or start/end of string).
///
/// The match starts at a word char (`[A-Za-z0-9_]`, not `-`): if the
/// first char of the run were `-`, `\b` at the start would require the
/// prev char to be a word char — but then the run could have started
/// earlier (at that prev word char) and we'd have already matched at
/// the earlier position in the single-pass loop. So restricting the
/// start to word chars is correct and avoids the `\b` edge case.
///
/// If the greedy run ends with `-` (non-word), `\b` at the end does NOT
/// hold (both sides non-word). We backtrack (shrink M) until we find a
/// match length where `\b` holds at `pos + M`. This mirrors Python's
/// regex backtracking on `\b[A-Za-z0-9_\-]{20,}\b`.
fn try_match_long_alphanumeric_run(rest: &str, input: &str, pos: usize) -> Option<usize> {
    let bytes = rest.as_bytes();
    if bytes.is_empty() {
        return None;
    }
    // The match must start at a word char ([A-Za-z0-9_]) for \b to hold
    // at `pos` (when the prev char is non-word or start of string — the
    // common case handled by the single-pass loop).
    if !is_python_word_char(bytes[0]) {
        return None;
    }
    // Count the greedy run of [A-Za-z0-9_\-] chars starting at pos.
    let mut greedy_len: usize = 1; // bytes[0] is already a word char
    while greedy_len < bytes.len() && is_long_run_char(bytes[greedy_len]) {
        greedy_len += 1;
    }
    if greedy_len < 20 {
        return None;
    }
    // Check \b at pos: prev char must be non-word (or start of string).
    // Combined with the `is_python_word_char(bytes[0])` check above,
    // this gives the correct Python \b semantics at the start.
    if !word_boundary_before(input, pos) {
        return None;
    }
    // Find the largest M with 20 <= M <= greedy_len such that \b holds
    // at pos+M. \b at pos+M holds iff word-ness differs between
    // input[pos+M-1] and input[pos+M] (or end of string). We scan
    // backwards from greedy_len (the greedy match) — for typical inputs
    // the run ends with a word char, so the first check succeeds and we
    // return immediately (O(1)).
    let mut m = greedy_len;
    while m >= 20 {
        let prev_word = is_python_word_char(input.as_bytes()[pos + m - 1]);
        let cur_word = pos + m < input.len() && is_python_word_char(input.as_bytes()[pos + m]);
        if prev_word != cur_word {
            return Some(m);
        }
        if m == 20 {
            break;
        }
        m -= 1;
    }
    None
}

/// Word-boundary check mirroring Python's `\b`. Returns true if the
/// byte at `pos - 1` is NOT an ASCII word char (`[A-Za-z0-9_]`), or if
/// `pos == 0`. We use byte indexing (not `chars()`) because all the
/// patterns that call this are ASCII-only — a multi-byte UTF-8 lead
/// byte (>= 0x80) is never an ASCII word char, so the check is sound
/// even when `pos` lands just after a non-ASCII character.
fn word_boundary_before(input: &str, pos: usize) -> bool {
    if pos == 0 {
        return true;
    }
    let prev = input.as_bytes()[pos - 1];
    !prev.is_ascii_alphanumeric() && prev != b'_'
}

/// Word-boundary check after a match. Returns true if the byte at
/// `pos` is NOT an ASCII word char, or if `pos >= input.len()`.
fn word_boundary_after(input: &str, pos: usize) -> bool {
    if pos >= input.len() {
        return true;
    }
    let next = input.as_bytes()[pos];
    !next.is_ascii_alphanumeric() && next != b'_'
}

/// Try to match an IBAN at the start of `rest`. Returns the match
/// length (in bytes) if the pattern matches and word boundaries are
/// satisfied, or `None` otherwise. The pattern is `[A-Z]{2}\d{2}[A-Z0-9]{10,30}`
/// (2 country letters + 2 check digits + 10-30 BBAN chars).
fn try_match_iban(rest: &str, input: &str, pos: usize) -> Option<usize> {
    if !word_boundary_before(input, pos) {
        return None;
    }
    let bytes = rest.as_bytes();
    // Need at least 2 letters + 2 digits + 10 BBAN = 14 bytes.
    if bytes.len() < 14 {
        return None;
    }
    if !bytes[0].is_ascii_uppercase() || !bytes[1].is_ascii_uppercase() {
        return None;
    }
    if !bytes[2].is_ascii_digit() || !bytes[3].is_ascii_digit() {
        return None;
    }
    let mut bban_len = 0usize;
    let max_bban = (bytes.len() - 4).min(30);
    for &b in &bytes[4..4 + max_bban] {
        if b.is_ascii_uppercase() || b.is_ascii_digit() {
            bban_len += 1;
        } else {
            break;
        }
    }
    if bban_len < 10 {
        return None;
    }
    let total = 4 + bban_len;
    if !word_boundary_after(input, pos + total) {
        return None;
    }
    Some(total)
}

/// Try to match a US phone number at the start of `rest`: 3-3-4 digits
/// with optional `-` or `.` separators between groups. Pattern:
/// `\d{3}[-.]?\d{3}[-.]?\d{4}`. Word boundaries required on both ends.
fn try_match_us_phone(rest: &str, input: &str, pos: usize) -> Option<usize> {
    if !word_boundary_before(input, pos) {
        return None;
    }
    let bytes = rest.as_bytes();
    let mut idx = 0usize;
    let group_sizes = [3usize, 3, 4];
    for (g, &expected) in group_sizes.iter().enumerate() {
        // Optional separator before groups 1 and 2 (not group 0).
        if g > 0 && idx < bytes.len() && (bytes[idx] == b'-' || bytes[idx] == b'.') {
            idx += 1;
        }
        let mut digits = 0usize;
        while idx < bytes.len() && bytes[idx].is_ascii_digit() && digits < expected {
            idx += 1;
            digits += 1;
        }
        if digits != expected {
            return None;
        }
    }
    if !word_boundary_after(input, pos + idx) {
        return None;
    }
    Some(idx)
}

/// Try to match an international phone at the start of `rest` (which
/// must begin with `+`). Pattern: `\+\d{1,3}[\s-]?\(?\d{1,4}\)?[\s-]?\d{3,4}[\s-]?\d{3,4}`.
/// Word boundary required AFTER the match (the `+` prefix already
/// guarantees a non-word char before).
fn try_match_intl_phone(rest: &str, input: &str, pos: usize) -> Option<usize> {
    debug_assert!(rest.starts_with('+'));
    // The `+` is a non-word char, so word_boundary_before is implied
    // (the previous char, if any, can't be a word char that joins to `+`).
    // `pos` is used below in the `word_boundary_after(input, pos + idx)`
    // check at the end of this function.
    let bytes = rest.as_bytes();
    let mut idx = 1usize; // skip the `+`

    // Country code: 1-3 digits.
    let cc_start = idx;
    while idx < bytes.len() && bytes[idx].is_ascii_digit() && idx - cc_start < 3 {
        idx += 1;
    }
    if idx == cc_start {
        return None;
    }

    // Optional separator (` ` or `-`).
    if idx < bytes.len() && (bytes[idx] == b' ' || bytes[idx] == b'-') {
        idx += 1;
    }
    // Optional `(`.
    if idx < bytes.len() && bytes[idx] == b'(' {
        idx += 1;
    }
    // Subscriber group 1: 1-4 digits.
    let g1_start = idx;
    while idx < bytes.len() && bytes[idx].is_ascii_digit() && idx - g1_start < 4 {
        idx += 1;
    }
    if idx == g1_start {
        return None;
    }
    // Optional `)`.
    if idx < bytes.len() && bytes[idx] == b')' {
        idx += 1;
    }
    // Optional separator.
    if idx < bytes.len() && (bytes[idx] == b' ' || bytes[idx] == b'-') {
        idx += 1;
    }
    // Subscriber group 2: 3-4 digits.
    let g2_start = idx;
    while idx < bytes.len() && bytes[idx].is_ascii_digit() && idx - g2_start < 4 {
        idx += 1;
    }
    if idx - g2_start < 3 {
        return None;
    }
    // Optional separator.
    if idx < bytes.len() && (bytes[idx] == b' ' || bytes[idx] == b'-') {
        idx += 1;
    }
    // Subscriber group 3: 3-4 digits.
    let g3_start = idx;
    while idx < bytes.len() && bytes[idx].is_ascii_digit() && idx - g3_start < 4 {
        idx += 1;
    }
    if idx - g3_start < 3 {
        return None;
    }
    if !word_boundary_after(input, pos + idx) {
        return None;
    }
    Some(idx)
}

/// Try to match an SSN at the start of `rest`: 3-2-4 digits with
/// REQUIRED `-` separators (the canonical `123-45-6789` form). Pattern:
/// `\d{3}-\d{2}-\d{4}`. Word boundaries required on both ends.
fn try_match_ssn(rest: &str, input: &str, pos: usize) -> Option<usize> {
    if !word_boundary_before(input, pos) {
        return None;
    }
    let bytes = rest.as_bytes();
    if bytes.len() < 11 {
        return None;
    }
    // 3 digits
    if !(bytes[0].is_ascii_digit() && bytes[1].is_ascii_digit() && bytes[2].is_ascii_digit()) {
        return None;
    }
    if bytes[3] != b'-' {
        return None;
    }
    // 2 digits
    if !(bytes[4].is_ascii_digit() && bytes[5].is_ascii_digit()) {
        return None;
    }
    if bytes[6] != b'-' {
        return None;
    }
    // 4 digits
    if !(bytes[7].is_ascii_digit()
        && bytes[8].is_ascii_digit()
        && bytes[9].is_ascii_digit()
        && bytes[10].is_ascii_digit())
    {
        return None;
    }
    if !word_boundary_after(input, pos + 11) {
        return None;
    }
    Some(11)
}

/// Try to match a credit-card number at the start of `rest`: 4-4-4-4
/// digits with optional `-` or space separators. Pattern:
/// `\d{4}[-\s]?\d{4}[-\s]?\d{4}[-\s]?\d{4}`. Word boundaries required
/// on both ends.
fn try_match_credit_card(rest: &str, input: &str, pos: usize) -> Option<usize> {
    if !word_boundary_before(input, pos) {
        return None;
    }
    let bytes = rest.as_bytes();
    let mut idx = 0usize;
    for group in 0..4usize {
        // Optional separator before groups 1, 2, 3.
        if group > 0 && idx < bytes.len() && (bytes[idx] == b'-' || bytes[idx] == b' ') {
            idx += 1;
        }
        // 4 digits.
        if idx + 4 > bytes.len() {
            return None;
        }
        for k in 0..4 {
            if !bytes[idx + k].is_ascii_digit() {
                return None;
            }
        }
        idx += 4;
    }
    if !word_boundary_after(input, pos + idx) {
        return None;
    }
    Some(idx)
}
