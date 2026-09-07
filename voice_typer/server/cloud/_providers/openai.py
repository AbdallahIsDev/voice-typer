"""OpenAI-compatible (OpenAI, Groq) multipart request shaping.

Extracted from the ``cloud_engines.py`` monolith. Pure shaping — no
I/O; the caller supplies the audio bytes and receives either the
ordered byte-chunk list or a streaming file-like body suitable for
``urllib.request.Request(data=...)``.
"""

from __future__ import annotations

from .._transport import _StreamingMultipartBody


def build_multipart_body(
    wav_bytes: bytes,
    filename: str,
    boundary: str,
    model_name: str,
    language: str,
) -> _StreamingMultipartBody:
    """Build multipart/form-data body for OpenAI-compatible APIs.

    PERF: the naive implementation concatenates all parts
    into a single ``bytes`` object via ``b"".join(parts)``, holding the
    full body (~5.2 MB for a 30s recording) as a SECOND contiguous
    block next to the WAV bytes already resident in ``parts``. This
    function returns a ``_StreamingMultipartBody`` file-like object
    that yields the parts as ~64 KB chunks on demand, avoiding that
    second full-body copy (the WAV itself stays resident in the parts
    list — only the joined duplicate is avoided).
    ``Content-Length`` is computed upfront so the server knows the
    total size without chunked transfer encoding.

    Tests may call ``b"payload" in body`` —
    ``_StreamingMultipartBody`` supports ``in`` via
    ``__contains__`` so assertions work without materializing
    the body through this API.
    """
    parts = build_multipart_parts(wav_bytes, filename, boundary, model_name, language)
    return _StreamingMultipartBody(parts)


def build_multipart_parts(
    wav_bytes: bytes,
    filename: str,
    boundary: str,
    model_name: str,
    language: str,
) -> list[bytes]:
    """Return the ordered list of byte chunks that compose the body."""
    parts: list[bytes] = []

    # file field
    parts.append(f"--{boundary}\r\n".encode())
    parts.append(f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'.encode())
    parts.append(b"Content-Type: audio/wav\r\n\r\n")
    parts.append(wav_bytes)
    parts.append(b"\r\n")

    # model field
    parts.append(f"--{boundary}\r\n".encode())
    parts.append(b'Content-Disposition: form-data; name="model"\r\n\r\n')
    parts.append(model_name.encode())
    parts.append(b"\r\n")

    # language field
    parts.append(f"--{boundary}\r\n".encode())
    parts.append(b'Content-Disposition: form-data; name="language"\r\n\r\n')
    parts.append(language.encode())
    parts.append(b"\r\n")

    # response_format
    parts.append(f"--{boundary}\r\n".encode())
    parts.append(b'Content-Disposition: form-data; name="response_format"\r\n\r\n')
    parts.append(b"json\r\n")

    parts.append(f"--{boundary}--\r\n".encode())
    return parts
