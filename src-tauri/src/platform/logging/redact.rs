//! PII redaction engine for log output (std-only, no `regex` dep).


pub(crate) fn redact_pii(input: &str) -> String {
    if !has_any_fast_trigger(input) {
        return input.to_string();
    }

    let mut out = String::with_capacity(input.len());
    let mut i = 0;
    while i < input.len() {
        let rest = &input[i..];

        if let Some(stripped) = rest.strip_prefix("Bearer ") {
            let token_len = stripped
                .find(|c: char| !is_api_token_char(c))
                .unwrap_or(stripped.len());
            out.push_str("Bearer ***");
            i += "Bearer ".len() + token_len;
            continue;
        }
        // 2. `Token <token>`: same charset as Bearer.
        if let Some(stripped) = rest.strip_prefix("Token ") {
            let token_len = stripped
                .find(|c: char| !is_api_token_char(c))
                .unwrap_or(stripped.len());
            out.push_str("Token ***");
            i += "Token ".len() + token_len;
            continue;
        }
        // 3. `sk-<token>`: token runs until non-alphanumeric / non
        // dash / non underscore (the typical API-key charset).
        if let Some(stripped) = rest.strip_prefix("sk-") {
            let token_len = stripped
                .find(|c: char| !c.is_ascii_alphanumeric() && c != '-' && c != '_')
                .unwrap_or(stripped.len());
            if token_len >= 8 {
                out.push_str("sk-***");
                i += "sk-".len() + token_len;
                continue;
            }
        }
        // 4. `gsk_<token>`: Groq-style API key. Same charset and
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

        if let Some((total_len, prefix_len)) = try_match_flag_or_bare_key(rest, input, i) {
            out.push_str(&rest[..prefix_len]);
            out.push_str("***");
            i += total_len;
            continue;
        }

        if rest.contains('@') {
            if let Some(at_pos) = rest.find('@') {
                let prefix = &rest[..at_pos];
                if prefix.contains(':') && !prefix.contains(' ') && !prefix.is_empty() {
                    out.push_str("***@");
                    i += at_pos + 1;
                    continue;
                }
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

        if let Some(iban_len) = try_match_iban(rest, input, i) {
            out.push_str("[IBAN]");
            i += iban_len;
            continue;
        }

        if rest.starts_with('+') {
            if let Some(phone_len) = try_match_intl_phone(rest, input, i) {
                out.push_str("[PHONE]");
                i += phone_len;
                continue;
            }
        }

        if let Some(phone_len) = try_match_us_phone(rest, input, i) {
            out.push_str("[PHONE]");
            i += phone_len;
            continue;
        }

        if let Some(ssn_len) = try_match_ssn(rest, input, i) {
            out.push_str("[SSN]");
            i += ssn_len;
            continue;
        }

        if let Some(cc_len) = try_match_credit_card(rest, input, i) {
            out.push_str("[CC]");
            i += cc_len;
            continue;
        }

        if let Some(run_len) = try_match_long_alphanumeric_run(rest, input, i) {
            out.push_str("***");
            i += run_len;
            continue;
        }

        if let Some(ch) = rest.chars().next() {
            out.push(ch);
            i += ch.len_utf8();
        } else {
            break;
        }
    }
    out
}

fn is_api_token_char(c: char) -> bool {
    c.is_ascii_alphanumeric() || c == '_' || c == '-' || c == '.' || c == '='
}

//flag-form + bare-keyword + 20+ char catch-all helpers ────────

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
    // `key` MUST be last: Python orders from most-specific to least-
    // specific so `api_key=` / `access_token=` / etc. win over `key=`.
    "key",
];

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

fn try_match_flag_or_bare_key(rest: &str, input: &str, pos: usize) -> Option<(usize, usize)> {
    // Pattern A: `--keyword=value` or `--keyword value`.
    // No `\b` required before `--` (Python's _FLAG_VALUE_PATTERN has none).
    if let Some(after_dashes) = rest.strip_prefix("--") {
        for &kw in SECRET_KEYWORDS {
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

fn try_match_long_alphanumeric_run(rest: &str, input: &str, pos: usize) -> Option<usize> {
    let bytes = rest.as_bytes();
    if bytes.is_empty() {
        return None;
    }
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
    if !word_boundary_before(input, pos) {
        return None;
    }
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

fn try_match_intl_phone(rest: &str, input: &str, pos: usize) -> Option<usize> {
    debug_assert!(rest.starts_with('+'));
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
