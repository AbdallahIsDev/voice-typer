"""Coverage for the media_ingest decoder / downloader / subtitles tiers (ADR-0023)."""

from __future__ import annotations

import math
import os
import struct
import sys
import types
import wave
from pathlib import Path

import numpy as np
import pytest
from voice_typer.server.media_ingest import decoder, downloader, errors as errors_mod, runtime as js_runtime, subtitles


def _write_wav(path: Path, seconds: float, rate: int = 16_000) -> None:
    frames = int(seconds * rate)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        payload = bytearray()
        for i in range(frames):
            sample = int(3000 * math.sin(2 * math.pi * 440 * i / rate))
            payload += struct.pack("<h", sample)
        wf.writeframes(bytes(payload))


class TestDecoderReal:
    def test_missing_local_file_raises_decode_failed(self, tmp_path):
        with pytest.raises(errors_mod.MediaIngestError) as info:
            list(decoder.decode_chunks(str(tmp_path / "nope.wav")))
        assert info.value.code == errors_mod.DECODE_FAILED

    def test_wav_yields_16k_mono_float32_chunks(self, tmp_path):
        wav = tmp_path / "clip.wav"
        _write_wav(wav, seconds=12.0)
        chunks = list(decoder.decode_chunks(str(wav)))
        assert len(chunks) >= 2
        first = chunks[0]
        assert first.shape == (decoder.CHUNK_SAMPLES,)
        assert first.dtype == np.float32
        total = sum(c.shape[0] for c in chunks)
        assert total >= 16_000 * 11  # resampling can drop a fraction at flush


class _FakeStream:
    def __init__(self, kind: str = "audio"):
        self.type = kind


class _FakeContainer:
    def __init__(self, streams, *, seek_error: Exception | None = None):
        self.streams = streams
        self._seek_error = seek_error

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def seek(self, offset):
        if self._seek_error is not None:
            raise self._seek_error

    def decode(self, stream):
        return iter(())


class TestDecoderFakes:
    def test_no_audio_stream_maps_no_audio(self, monkeypatch):
        import av

        fake = _FakeContainer([_FakeStream("video")])
        monkeypatch.setattr(av, "open", lambda *a, **k: fake)
        with pytest.raises(errors_mod.MediaIngestError) as info:
            list(decoder.decode_chunks("whatever.mp4", remote=True))
        assert info.value.code == errors_mod.NO_AUDIO

    def test_seek_failure_maps_stream_stalled(self, monkeypatch):
        import av

        fake = _FakeContainer([_FakeStream()], seek_error=RuntimeError("boom"))
        monkeypatch.setattr(av, "open", lambda *a, **k: fake)
        with pytest.raises(errors_mod.MediaIngestError) as info:
            list(decoder.decode_chunks("remote.mp4", remote=True, start_seconds=10.0))
        assert info.value.code == errors_mod.STREAM_STALLED

    def test_open_timeout_maps_stream_stalled(self, monkeypatch):
        import av

        def _raise(*a, **k):
            raise TimeoutError("timed out")

        monkeypatch.setattr(av, "open", _raise)
        with pytest.raises(errors_mod.MediaIngestError) as info:
            list(decoder.decode_chunks("https://cdn/x.mp4", remote=True))
        assert info.value.code == errors_mod.STREAM_STALLED

    def test_unknown_open_error_maps_decode_failed(self, monkeypatch):
        import av

        def _raise(*a, **k):
            raise ValueError("garbled")

        monkeypatch.setattr(av, "open", _raise)
        with pytest.raises(errors_mod.MediaIngestError) as info:
            list(decoder.decode_chunks("https://cdn/x.mp4", remote=True))
        assert info.value.code == errors_mod.DECODE_FAILED

    def test_remote_open_receives_timeouts(self, monkeypatch):
        import av

        captured = {}
        fake = _FakeContainer([_FakeStream()])

        def _open(source, options=None):
            captured["options"] = options
            return fake

        monkeypatch.setattr(av, "open", _open)
        list(decoder.decode_chunks("https://cdn/x.mp4", remote=True))
        assert captured["options"] == {
            "open_timeout": str(decoder.OPEN_TIMEOUT_US),
            "read_timeout": str(decoder.READ_TIMEOUT_US),
        }


def _stub_yt_dlp(monkeypatch, youtube_dl_cls) -> None:
    module = types.ModuleType("yt_dlp")
    module.YoutubeDL = youtube_dl_cls
    monkeypatch.setitem(sys.modules, "yt_dlp", module)


class TestTempAudioDownload:
    def test_missing_yt_dlp_maps_download_failed(self, monkeypatch):
        monkeypatch.setitem(sys.modules, "yt_dlp", None)
        with pytest.raises(errors_mod.MediaIngestError) as info, downloader.temp_audio_download("https://x"):
            pass
        assert info.value.code == errors_mod.DOWNLOAD_FAILED

    def test_success_yields_file_then_cleans_up(self, monkeypatch):
        _stub_yt_dlp(monkeypatch, object)

        class FakeYDL:
            def __init__(self, options):
                self.options = options

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def download(self, urls):
                out = Path(self.options["outtmpl"].replace("%(title).64s", "clip").replace("%(ext)s", "m4a"))
                out.write_bytes(b"audio-bytes")

        with downloader.temp_audio_download("https://x", factory=lambda o: FakeYDL(o)) as path:
            assert path.is_file()
            assert path.read_bytes() == b"audio-bytes"
            held = path
        assert not held.exists()
        assert not held.parent.exists()

    def test_download_error_maps_download_failed(self, monkeypatch):
        _stub_yt_dlp(monkeypatch, object)

        class FakeYDL:
            def __init__(self, options):
                pass

            def download(self, urls):
                raise RuntimeError("403")

        with (
            pytest.raises(errors_mod.MediaIngestError) as info,
            downloader.temp_audio_download("https://x", factory=lambda o: FakeYDL(o)),
        ):
            pass
        assert info.value.code == errors_mod.DOWNLOAD_FAILED

    def test_no_file_produced_maps_download_failed(self, monkeypatch):
        _stub_yt_dlp(monkeypatch, object)

        class FakeYDL:
            def __init__(self, options):
                pass

            def download(self, urls):
                return None

        with (
            pytest.raises(errors_mod.MediaIngestError) as info,
            downloader.temp_audio_download("https://x", factory=lambda o: FakeYDL(o)),
        ):
            pass
        assert info.value.code == errors_mod.DOWNLOAD_FAILED

    def test_progress_hook_reports_fraction(self, monkeypatch):
        _stub_yt_dlp(monkeypatch, object)

        hooks = []

        class FakeYDL:
            def __init__(self, options):
                self.options = options
                hooks.extend(options["progress_hooks"])

            def download(self, urls):
                out_dir = Path(self.options["outtmpl"]).parent
                (out_dir / "clip.m4a").write_bytes(b"x")

        fractions: list[float] = []
        with downloader.temp_audio_download("https://x", factory=lambda o: FakeYDL(o), on_progress=fractions.append):
            pass
        assert hooks
        hooks[0]({"status": "downloading", "total_bytes": 200, "downloaded_bytes": 50})
        hooks[0]({"status": "downloading", "total_bytes_estimate": 100, "downloaded_bytes": 150})
        hooks[0]({"status": "finished", "total_bytes": 200, "downloaded_bytes": 200})
        assert fractions == [0.25, 1.0]


class TestSubtitles:
    def test_pick_language_prefers_exact(self):
        subs = {"en": [{"url": "a"}], "de": [{"url": "b"}]}
        assert subtitles._pick_language(subs, "de") == [{"url": "b"}]

    def test_pick_language_falls_back_to_en(self):
        subs = {"en": [{"url": "a"}], "fr": [{"url": "c"}]}
        assert subtitles._pick_language(subs, "de") == [{"url": "a"}]


class TestPackDenoDiscovery:
    def _make_pack(self, root: Path, *, with_deno: bool = True) -> str:
        import hashlib
        import json as _json

        exe = "deno.exe" if sys.platform == "win32" else "deno"
        pack = root / "1"
        if with_deno:
            (pack / "bin").mkdir(parents=True)
            payload = b"fake-deno"
            member = f"bin/{exe}"
            (pack / "bin" / exe).write_bytes(payload)
        else:
            pack.mkdir(parents=True)
            payload = b"placeholder"
            member = "readme.txt"
            (pack / "readme.txt").write_bytes(payload)
        manifest = {
            "version": "1",
            "sha256": "0" * 64,
            "min_proto_version": 1,
            "files": [
                {
                    "name": member,
                    "sha256": hashlib.sha256(payload).hexdigest(),
                    "size": len(payload),
                }
            ],
        }
        (pack / "pack-manifest.json").write_text(_json.dumps(manifest), encoding="utf-8")
        return exe

    def test_pack_shipped_deno_wins_over_path(self, tmp_path):
        exe = self._make_pack(tmp_path)

        def _which(name, path=None):
            return "/usr/bin/deno" if name == "deno" else None

        found = js_runtime.find_js_runtime(which=_which, pack_root=tmp_path)
        assert found is not None and found.name == "deno"
        assert found.path.endswith(f"bin{os.sep}{exe}")
        assert js_runtime.js_runtime_options(found) == {"deno": found.path}

    def test_pack_without_deno_falls_back_to_path(self, tmp_path):
        self._make_pack(tmp_path, with_deno=False)

        def _which(name, path=None):
            return "/usr/bin/deno" if name == "deno" else None

        found = js_runtime.find_js_runtime(which=_which, pack_root=tmp_path)
        assert found is not None and found.path == "/usr/bin/deno"

    def test_no_pack_probe_returns_none(self, tmp_path):
        assert js_runtime._pack_deno_path(tmp_path) is None

    def test_probe_never_raises_on_bad_root(self, tmp_path):
        broken = tmp_path / "missing-root"
        assert js_runtime._pack_deno_path(broken) is None

    def test_pick_language_none_when_absent(self):
        assert subtitles._pick_language({"fr": [{"url": "c"}]}, "de") is None
        assert subtitles._pick_language({}, "en") is None
        assert subtitles._pick_language(None, "en") is None

    def test_clean_caption_text_strips_vtt_scaffolding(self):
        raw = "\n".join(
            [
                "WEBVTT",
                "Kind: captions",
                "Language: en",
                "",
                "1",
                "00:00:00.000 --> 00:00:02.000",
                "<i>Hello there</i>",
                "00:00:02.000 --> 00:00:04.000",
                "Hello there",
                "00:00:04.000 --> 00:00:06.000",
                "general Kenobi",
            ]
        )
        assert subtitles._clean_caption_text(raw) == "Hello there general Kenobi"

    def test_clean_caption_text_empty_returns_none(self):
        assert subtitles._clean_caption_text("WEBVTT\n\n00:00:00.000 --> 00:00:01.000\n") is None

    def test_fetch_subtitles_missing_yt_dlp_returns_none(self, monkeypatch):
        monkeypatch.setitem(sys.modules, "yt_dlp", None)
        assert subtitles.fetch_subtitles("https://x") is None

    def test_fetch_subtitles_extractor_failure_returns_none(self, monkeypatch):
        _stub_yt_dlp(monkeypatch, object)

        class BoomYDL:
            def __init__(self, options):
                pass

            def extract_info(self, url, download=False):
                raise RuntimeError("nope")

        assert subtitles.fetch_subtitles("https://x", factory=lambda o: BoomYDL(o)) is None

    def test_fetch_subtitles_happy_path(self, monkeypatch):
        _stub_yt_dlp(monkeypatch, object)

        vtt = "WEBVTT\n\n00:00:00.000 --> 00:00:01.000\nhello world\n"

        class InfoYDL:
            def __init__(self, options):
                pass

            def extract_info(self, url, download=False):
                return {"subtitles": {"en": [{"url": "https://subs/en.vtt"}]}}

        class _Resp:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self, n):
                return vtt.encode("utf-8")

        monkeypatch.setattr("urllib.request.urlopen", lambda url, timeout=30.0: _Resp())
        assert subtitles.fetch_subtitles("https://x", factory=lambda o: InfoYDL(o)) == "hello world"

    def test_fetch_subtitles_skips_empty_format_urls(self, monkeypatch):
        _stub_yt_dlp(monkeypatch, object)

        class InfoYDL:
            def __init__(self, options):
                pass

            def extract_info(self, url, download=False):
                return {"subtitles": {"en": [{"url": ""}, {}]}}

        assert subtitles.fetch_subtitles("https://x", factory=lambda o: InfoYDL(o)) is None
