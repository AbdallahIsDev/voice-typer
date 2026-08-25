"""Sentence capitalization + file-extension repair rules for text cleanup.

Split verbatim out of the pre-split ``text_cleanup`` module.
"""

from __future__ import annotations

import re


def _capitalize_sentences(text: str) -> str:
    chars = list(text)
    capitalize_next = True
    for index, char in enumerate(chars):
        if char.isalpha():
            if capitalize_next:
                chars[index] = char.upper()
            capitalize_next = False
        elif char in ".!?":
            capitalize_next = True
    return "".join(chars)


# ─── M2: File extension fix ──────────────────────────────────────────────

_KNOWN_EXTENSIONS = {
    ".txt",
    ".md",
    ".exe",
    ".py",
    ".pdf",
    ".doc",
    ".docx",
    ".xls",
    ".xlsx",
    ".ppt",
    ".pptx",
    ".csv",
    ".json",
    ".xml",
    ".html",
    ".css",
    ".js",
    ".ts",
    ".bat",
    ".sh",
    ".ps1",
    ".cmd",
    ".msi",
    ".dll",
    ".zip",
    ".rar",
    ".7z",
    ".mp3",
    ".mp4",
    ".avi",
    ".wav",
    ".jpg",
    ".jpeg",
    ".png",
    ".gif",
    ".svg",
    ".ico",
    ".log",
    ".ini",
    ".cfg",
    ".yaml",
    ".yml",
    ".toml",
    ".db",
    ".sqlite",
    ".bak",
    ".tmp",
    ".sys",
    ".mov",
    ".mkv",
    ".webm",
    ".flac",
    ".ogg",
    ".webp",
    ".bmp",
    ".tiff",
    ".psd",
    ".ai",
}

# _fix_file_extensions compiled pattern
_RE_FILE_EXT = re.compile(r"(\w+)\.\s+([a-zA-Z]{2,4})\b")


def _fix_file_extensions(text: str) -> str:
    """Fix file extension patterns corrupted by text cleanup.

    Whisper transcribes 'features dot md' as 'features. md'. The cleanup
    pipeline then capitalizes after the period: 'features. Md'. This function
    collapses such patterns back to 'features.md' before capitalization runs.

    Must not break:
    - Sentence-ending periods (normal text)
    - URLs (example.com)
    - Abbreviations (U.S.A., Dr., etc.)
    """

    # Pattern: word. ext or word . ext or word .ext
    # Match: word characters followed by optional space, dot, optional space, 2-4 letter extension
    def _replace_extension(m):
        before = m.group(1)  # word before the dot
        ext = m.group(2)  # extension without leading dot
        # Only collapse if the extension is a known file extension
        if f".{ext.lower()}" in _KNOWN_EXTENSIONS:
            return f"{before}.{ext.lower()}"
        # Not a known extension — leave as-is
        return m.group(0)

    # Match word. ext  (e.g., "features. md")
    # PERF-004: use precompiled pattern
    text = _RE_FILE_EXT.sub(
        _replace_extension,
        text,
    )
    return text
