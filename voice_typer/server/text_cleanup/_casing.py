"""Casing fixes for cleaned tokens."""

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
    """Fix file extension patterns corrupted by text cleanup."""

    # Pattern: word. ext or word . ext or word .ext
    def _replace_extension(m):
        before = m.group(1)
        ext = m.group(2)
        if f".{ext.lower()}" in _KNOWN_EXTENSIONS:
            return f"{before}.{ext.lower()}"
        return m.group(0)

    # PERF-004: use precompiled pattern
    text = _RE_FILE_EXT.sub(
        _replace_extension,
        text,
    )
    return text
