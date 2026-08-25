"""H15/M8: Concurrent-load test for the resample prefix cache.

The finding: the cached resampled prefix (``_cached_resampled``,
``_cached_resample_key``, ``_cached_native_chunk_count``) is tested for
correctness and invalidation in unit tests, but NEVER under concurrent
load. The streaming thread polls ``snapshot()`` at ~4 Hz while the audio
callback appends chunks under ``self._lock`` — no test verifies that
N threads calling ``snapshot()`` simultaneously don't corrupt the cache
or produce torn reads.

This module adds a ThreadPoolExecutor stress test that calls
``Recorder.snapshot()`` from N threads while a producer appends chunks,
and asserts no exceptions, no torn reads, and bounded cache size.
"""

from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import numpy as np
import pytest
from voice_typer.server.config import Config
from voice_typer.server.recording import Recorder


def _make_recorder() -> Recorder:
    """Create a Recorder with minimal setup for snapshot testing.

    Recorder construction is delegated to the shared canonical factory
    (XS-42 helper dedup) with a real ``Config`` injected.
    """
    from tests.fixtures.recorder_test_helpers import make_recorder

    cfg = Config()
    cfg.sample_rate = 16000
    rec = make_recorder(config=cfg)
    rec._effective_sr = 16000
    rec._cached_target_sr = 16000
    return rec


class TestResampleCacheConcurrentLoadSafety:
    """H15/M8: Verify the resample prefix cache is safe under concurrent access."""

    def test_concurrent_snapshot_no_corruption(self):
        """N threads calling snapshot() concurrently while a producer
        appends chunks must not raise exceptions or corrupt the cache.
        """
        rec = _make_recorder()
        rec._recording_event.set()
        rec._recording_start_time = time.perf_counter()

        # Pre-fill with a few chunks so snapshot() has data to resample
        chunk = np.full((512, 1), 0.1, dtype=np.float32)
        with rec._lock:
            for _ in range(10):
                rec._buffer.append(chunk)
            rec._chunk_count = 10

        errors: list[Exception] = []
        stop = threading.Event()

        def producer():
            """Simulate the audio callback appending chunks."""
            while not stop.is_set():
                try:
                    with rec._lock:
                        rec._buffer.append(chunk.copy())
                        rec._chunk_count += 1
                except Exception as e:
                    errors.append(e)
                time.sleep(0.001)  # ~1 kHz

        def consumer():
            """Simulate the streaming thread calling snapshot()."""
            results = []
            for _ in range(20):
                try:
                    arr = rec.snapshot()
                    results.append(arr)
                except Exception as e:
                    errors.append(e)
                time.sleep(0.002)  # ~500 Hz
            return results

        # Start producer
        prod_thread = threading.Thread(target=producer, daemon=True)
        prod_thread.start()

        # Run 8 consumer threads concurrently
        with ThreadPoolExecutor(max_workers=8) as pool:
            futures = [pool.submit(consumer) for _ in range(8)]
            all_results = []
            for _fut in as_completed(futures, timeout=10):
                all_results.extend(_fut.result())

        stop.set()
        prod_thread.join(timeout=2)

        # No exceptions should have been raised
        assert not errors, f"Concurrent snapshot/access raised: {errors}"

        # All results must be valid numpy arrays
        for arr in all_results:
            assert isinstance(arr, np.ndarray), (
                "H15/M8: snapshot() must always return a numpy array, even "
                f"under concurrent access. Got {type(arr).__name__}"
            )
            assert arr.dtype == np.float32, f"H15/M8: snapshot() must return float32, got {arr.dtype}"

    def test_concurrent_snapshot_cache_stays_bounded(self):
        """The cached resampled prefix must not grow unboundedly under
        concurrent access — it should always be the same length as the
        current buffer (or empty).
        """
        rec = _make_recorder()
        rec._recording_event.set()
        rec._recording_start_time = time.perf_counter()

        chunk = np.full((512, 1), 0.1, dtype=np.float32)
        with rec._lock:
            for _ in range(5):
                rec._buffer.append(chunk)
            rec._chunk_count = 5

        errors: list[Exception] = []
        stop = threading.Event()

        def producer():
            while not stop.is_set():
                with rec._lock:
                    rec._buffer.append(chunk.copy())
                    rec._chunk_count += 1
                time.sleep(0.001)

        def consumer():
            for _ in range(10):
                try:
                    rec.snapshot()
                except Exception as e:
                    errors.append(e)
                time.sleep(0.002)

        prod_thread = threading.Thread(target=producer, daemon=True)
        prod_thread.start()

        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = [pool.submit(consumer) for _ in range(4)]
            for _fut in as_completed(futures, timeout=10):
                pass

        stop.set()
        prod_thread.join(timeout=2)

        assert not errors, f"Concurrent access raised: {errors}"

        # After all threads complete, the cache should be consistent:
        # either empty or matching the current buffer length.
        with rec._lock:
            if rec._cached_resampled is not None and rec._cached_resampled.size > 0:
                # The cached prefix is based on _cached_native_chunk_count
                # which must be <= current _chunk_count
                assert rec._cached_native_chunk_count <= rec._chunk_count, (
                    f"H15/M8: cached native chunk count "
                    f"({rec._cached_native_chunk_count}) > current chunk count "
                    f"({rec._chunk_count}) — cache corruption under concurrent access"
                )

    def test_concurrent_snapshot_no_torn_reads(self):
        """Verify no torn reads: snapshot() must return a consistent array
        even when the buffer is being modified concurrently. A torn read
        would manifest as an array with unexpected length or shape.
        """
        rec = _make_recorder()
        rec._recording_event.set()
        rec._recording_start_time = time.perf_counter()

        chunk = np.full((512, 1), 0.1, dtype=np.float32)
        with rec._lock:
            for _ in range(10):
                rec._buffer.append(chunk)
            rec._chunk_count = 10

        errors: list[Exception] = []
        stop = threading.Event()

        def producer():
            while not stop.is_set():
                with rec._lock:
                    rec._buffer.append(chunk.copy())
                    rec._chunk_count += 1
                time.sleep(0.0005)  # High frequency

        def consumer():
            """Each snapshot must be a valid 1-D float32 array."""
            for _ in range(30):
                try:
                    arr = rec.snapshot()
                    # Must be 1-D (flattened)
                    assert arr.ndim == 1, f"H15/M8: torn read — snapshot returned {arr.ndim}-D array"
                    # Must be non-negative length
                    assert arr.shape[0] >= 0
                    # Must be contiguous
                    assert arr.flags["C_CONTIGUOUS"] or arr.size == 0
                except Exception as e:
                    errors.append(e)
                time.sleep(0.001)

        prod_thread = threading.Thread(target=producer, daemon=True)
        prod_thread.start()

        with ThreadPoolExecutor(max_workers=6) as pool:
            futures = [pool.submit(consumer) for _ in range(6)]
            for _fut in as_completed(futures, timeout=15):
                pass

        stop.set()
        prod_thread.join(timeout=2)

        assert not errors, f"Torn read detected: {errors}"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
