"""§8.17 — Pack download competes with model download: shared queue.

Spec (§8.17):

  A shared download queue (``download_queue.py``, new). The pack is
  always lowest-priority and pauses while a user-initiated download
  runs. Both are resumable.

Tested behaviors:

  1. ``PackDownloadQueue`` starts with no active user downloads.
  2. ``user_download_started`` increments the active count + sets the
     pack pause flag.
  3. ``user_download_finished`` decrements the count; when it reaches
     zero, clears the pause flag.
  4. Multiple concurrent user downloads — pause flag stays set until
     ALL finish.
  5. ``pack_should_pause`` reflects the pause flag.
  6. ``pack_wait_for_resume`` blocks (up to timeout) while paused,
     returns True when un-paused.
  7. The pause flag is thread-safe.
"""

from __future__ import annotations

import threading
import time

import pytest
from voice_typer.server.service import offline_pack


class TestPackDownloadQueue:
    """§8.17 — shared download queue; pack is lowest-priority."""

    def test_starts_unpaused(self):
        q = offline_pack.OfflinePackDownloadQueue()
        assert q.user_active == 0
        assert q.pack_should_pause() is False

    def test_user_download_started_sets_pause(self):
        q = offline_pack.OfflinePackDownloadQueue()
        q.user_download_started()
        assert q.user_active == 1
        assert q.pack_should_pause() is True

    def test_user_download_finished_clears_pause(self):
        q = offline_pack.OfflinePackDownloadQueue()
        q.user_download_started()
        q.user_download_finished()
        assert q.user_active == 0
        assert q.pack_should_pause() is False

    def test_multiple_user_downloads_keep_paused(self):
        """Two concurrent user downloads — pause stays until BOTH finish."""
        q = offline_pack.OfflinePackDownloadQueue()
        q.user_download_started()
        q.user_download_started()
        assert q.user_active == 2
        q.user_download_finished()
        assert q.user_active == 1
        assert q.pack_should_pause() is True  # still paused
        q.user_download_finished()
        assert q.user_active == 0
        assert q.pack_should_pause() is False

    def test_user_download_finished_does_not_go_negative(self):
        """A spurious ``user_download_finished`` (without a matching
        ``user_download_started``) must NOT make the count negative."""
        q = offline_pack.OfflinePackDownloadQueue()
        q.user_download_finished()  # spurious
        assert q.user_active == 0
        assert q.pack_should_pause() is False

    def test_pack_wait_for_resume_returns_true_when_unpaused(self):
        """``pack_wait_for_resume`` blocks then returns True when the
        pause flag clears."""
        q = offline_pack.OfflinePackDownloadQueue()
        q.user_download_started()

        def releaser():
            time.sleep(0.1)
            q.user_download_finished()

        threading.Thread(target=releaser).start()
        cleared = q.pack_wait_for_resume(timeout_s=2.0)
        assert cleared is True

    def test_pack_wait_for_resume_returns_false_on_timeout(self):
        """When the pause doesn't clear within the timeout, returns False."""
        q = offline_pack.OfflinePackDownloadQueue()
        q.user_download_started()
        cleared = q.pack_wait_for_resume(timeout_s=0.1)
        assert cleared is False  # still paused

    def test_thread_safety(self):
        """Concurrent ``user_download_started`` / ``user_download_finished``
        calls don't race — the count is consistent."""
        q = offline_pack.OfflinePackDownloadQueue()

        def worker():
            for _ in range(100):
                q.user_download_started()
                q.user_download_finished()

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5.0)
        # After all workers finish, the count should be 0 (every start
        # had a matching finish).
        assert q.user_active == 0
        assert q.pack_should_pause() is False


if __name__ == "__main__":
    pytest.main([__file__, "-x"])
