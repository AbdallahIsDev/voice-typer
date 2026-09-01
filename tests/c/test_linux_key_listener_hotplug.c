/* =============================================================================
 * Voice Typer — Linux native key-listener hotplug unit test (C-level)
 *
 * Compiles the production listener source (``linux-key-listener.c``) INTO this
 * test translation unit via ``#include`` (same convention as
 * ``test_linux_key_listener_dedup.c``) so the file-local static helpers for
 * hotplug become reachable: ``is_event_node_name``, ``find_device_index_*``,
 * ``add_device_by_name``, ``remove_device_at`` / ``remove_device_by_name``,
 * ``handle_inotify_events``. The listener's own ``main`` is renamed out of the
 * way via a ``#define main listener_main`` macro so this file owns the entry
 * point.
 *
 * Build (mirror of what the Python integration test runs):
 *   gcc -O2 -std=c99 -Wall -Wextra -Wno-unused-function \
 *       tests/c/test_linux_key_listener_hotplug.c -o /tmp/test_hotplug
 *
 * Coverage layers:
 *   1. Pure logic: event-node name filtering, tracked-set lookup, compaction
 *      on removal, capacity guard, idempotent add (IN_ATTRIB re-fire).
 *   2. Real inotify wiring: a watch is placed on a mkdtemp() directory and
 *      ``handle_inotify_events`` is driven by REAL kernel events (create /
 *      rename / unlink / chmod) — the remove-by-name path is exercised fully
 *      end-to-end; the add path's graceful-skip (open of a nonexistent
 *      /dev/input node) and the unknown-name ignore path are exercised too.
 *   3. Degrade path: ``setup_hotplug_watch`` on a machine without /dev/input
 *      leaves g_inotify_fd == -1 (hotplug disabled) WITHOUT crashing.
 *
 * No real /dev/input device is required; tracked fds are populated as -1
 * (never closed) so the test never touches real file descriptors.
 *
 * SPDX-License-Identifier: MIT
 * =============================================================================
 */

/* Define _GNU_SOURCE BEFORE any system header so the glibc extensions used by
 * the listener source (strtok_r, sigaction, sigemptyset, etc.) are visible.
 * The listener source re-defines _GNU_SOURCE to the same (empty) value, which
 * is allowed by the C standard and produces no warning. */
#ifndef _GNU_SOURCE
#define _GNU_SOURCE
#endif

#include <assert.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

/* Pull the production source into this translation unit so its file-local
 * static helpers are reachable. The listener's ``main`` is renamed to
 * ``listener_main`` via a macro so this file owns the entry point below. */
#define main listener_main
#include "../../voice_typer/server/native/linux-key-listener.c"
#undef main

/* ─── Helpers ─────────────────────────────────────────────────────────── */

static void reset_tracked_devices(void) {
    close_devices(); /* closes only fd >= 0; fake entries are -1 */
    assert(g_num_fds == 0);
}

/* Track a fake device (fd -1 → never actually closed). */
static void track_fake_device(const char *name) {
    assert(g_num_fds < MAX_DEVICES);
    g_fds[g_num_fds] = -1;
    snprintf(g_dev_names[g_num_fds], DEV_NAME_LEN, "%s", name);
    g_num_fds++;
}

/* Real inotify watch on a scratch directory, mirroring the production mask
 * (setup_hotplug_watch itself watches /dev/input, which may not exist in the
 * test environment, so the harness wires g_inotify_fd manually). */
static int watch_scratch_dir(const char *dir) {
    int fd = inotify_init1(IN_NONBLOCK | IN_CLOEXEC);
    assert(fd >= 0);
    uint32_t mask = IN_CREATE | IN_MOVED_TO | IN_ATTRIB | IN_DELETE | IN_MOVED_FROM;
    assert(inotify_add_watch(fd, dir, mask) >= 0);
    return fd;
}

static void unwatch_scratch_dir(int fd) {
    if (fd >= 0) {
        close(fd);
    }
}

/* Create a plain file inside the scratch dir (generates IN_CREATE). */
static void create_file(const char *dir, const char *name) {
    char path[512];
    snprintf(path, sizeof(path), "%s/%s", dir, name);
    int fd = open(path, O_WRONLY | O_CREAT | O_TRUNC, 0600);
    assert(fd >= 0);
    close(fd);
}

static void unlink_file(const char *dir, const char *name) {
    char path[512];
    snprintf(path, sizeof(path), "%s/%s", dir, name);
    assert(unlink(path) == 0);
}

static void rename_file(const char *dir, const char *from, const char *to) {
    char pfrom[512], pto[512];
    snprintf(pfrom, sizeof(pfrom), "%s/%s", dir, from);
    snprintf(pto, sizeof(pto), "%s/%s", dir, to);
    assert(rename(pfrom, pto) == 0);
}

/* "event99999" passes is_event_node_name but is far outside the kernel's
 * evdev minor-number range, so /dev/input/event99999 cannot exist as a real
 * node on any Linux system — the open in add_device_by_name deterministically
 * fails and the graceful-skip path is what gets exercised. */
#define IMPOSSIBLE_NODE "event99999"

int main(void) {
    char scratch_tpl[] = "/tmp/vt_hotplug_test_XXXXXX";
    char *scratch = mkdtemp(scratch_tpl);
    assert(scratch != NULL);

    /* ── 1. Event-node name filtering ─────────────────────────────────── */
    {
        assert(is_event_node_name("event0") == 1);
        assert(is_event_node_name("event7") == 1);
        assert(is_event_node_name("event42") == 1);
        assert(is_event_node_name("event123456") == 1);
        assert(is_event_node_name("mice") == 0);
        assert(is_event_node_name("mouse0") == 0);
        assert(is_event_node_name("js0") == 0);
        assert(is_event_node_name("event") == 0);      /* bare, no digits */
        assert(is_event_node_name("eventx") == 0);     /* non-digit suffix */
        assert(is_event_node_name("event9x") == 0);    /* partial digits */
        assert(is_event_node_name("EVENT7") == 0);     /* case-sensitive */
        assert(is_event_node_name("") == 0);
        assert(is_event_node_name(NULL) == 0);
        char longname[DEV_NAME_LEN + 16];
        memset(longname, '7', sizeof(longname) - 1);
        longname[sizeof(longname) - 1] = '\0';
        memcpy(longname, "event", 5);
        assert(is_event_node_name(longname) == 0); /* exceeds DEV_NAME_LEN */
    }

    /* ── 2. Tracked-set lookup by name and by fd ───────────────────────── */
    {
        reset_tracked_devices();
        track_fake_device("event3");
        track_fake_device("event11");
        track_fake_device("event17");
        assert(g_num_fds == 3);

        assert(find_device_index_by_name("event3") == 0);
        assert(find_device_index_by_name("event11") == 1);
        assert(find_device_index_by_name("event17") == 2);
        assert(find_device_index_by_name("event4") == -1);   /* not tracked */
        assert(find_device_index_by_name("mice") == -1);

        assert(find_device_index_by_fd(-1) == 0); /* first fake fd == -1 */
        assert(find_device_index_by_fd(12345) == -1);
        reset_tracked_devices();
        assert(g_num_fds == 0);
    }

    /* ── 3. Removal compacts the parallel fd + name arrays ─────────────── */
    {
        reset_tracked_devices();
        track_fake_device("event3");
        track_fake_device("event11");
        track_fake_device("event17");

        remove_device_at(1); /* drop the middle entry */
        assert(g_num_fds == 2);
        assert(strcmp(g_dev_names[0], "event3") == 0);
        assert(strcmp(g_dev_names[1], "event17") == 0); /* shifted down */
        assert(g_dev_names[2][0] == '\0');              /* tail cleared */
        assert(g_fds[2] == -1);

        remove_device_at(0); /* drop the head */
        assert(g_num_fds == 1);
        assert(strcmp(g_dev_names[0], "event17") == 0);

        remove_device_at(1);  /* out of range — no-op, no crash */
        remove_device_at(-1); /* out of range — no-op, no crash */
        assert(g_num_fds == 1);

        remove_device_by_name("event17");
        assert(g_num_fds == 0);
        remove_device_by_name("event17"); /* not tracked — no-op, no crash */
        assert(g_num_fds == 0);
        reset_tracked_devices();
    }

    /* ── 4. Idempotent add: re-adding a tracked name is a no-op ────────── */
    /* IN_ATTRIB re-fires when udev adjusts node permissions; the add path
     * must not open or track the device a second time. */
    {
        reset_tracked_devices();
        track_fake_device("event5");
        int before = g_num_fds;
        assert(add_device_by_name("event5") == 0);
        assert(g_num_fds == before);
        reset_tracked_devices();
    }

    /* ── 5. Capacity guard: MAX_DEVICES reached → add is refused before
     * attempting any open (deterministic, no /dev/input access needed). */
    {
        reset_tracked_devices();
        for (int i = 0; i < MAX_DEVICES; i++) {
            char name[DEV_NAME_LEN];
            snprintf(name, sizeof(name), "event%d", i);
            track_fake_device(name);
        }
        assert(g_num_fds == MAX_DEVICES);
        assert(add_device_by_name("event777") == 0);
        assert(g_num_fds == MAX_DEVICES);
        reset_tracked_devices();
    }

    /* ── 6. Graceful skip: open failure on hotplug-add is not fatal ────── */
    /* Uses a node name that cannot exist as a real device (see
     * IMPOSSIBLE_NODE above), so open() fails on every system. */
    {
        reset_tracked_devices();
        assert(add_device_by_name(IMPOSSIBLE_NODE) == 0);
        assert(g_num_fds == 0); /* skipped, not added, no crash */
        assert(add_device_by_name("mice") == 0); /* non-node: filtered out */
        assert(g_num_fds == 0);
        reset_tracked_devices();
    }

    /* ── 7. REAL inotify wiring: kernel events drive the tracked set ───── */
    {
        reset_tracked_devices();
        track_fake_device("event42"); /* pretend it was opened at startup */
        track_fake_device("event43");

        int wfd = watch_scratch_dir(scratch);
        g_inotify_fd = wfd;

        /* IN_MOVED_TO of a non-node name ("mouse0") must be ignored. */
        create_file(scratch, "staging1");
        rename_file(scratch, "staging1", "mouse0");
        handle_inotify_events();
        assert(g_num_fds == 2);

        /* IN_CREATE of a non-node name ("mice") must be ignored. */
        create_file(scratch, "mice");
        handle_inotify_events();
        assert(g_num_fds == 2);

        /* IN_ATTRIB on a non-node name must be ignored. */
        {
            char path[512];
            snprintf(path, sizeof(path), "%s/mice", scratch);
            assert(chmod(path, 0644) == 0);
        }
        handle_inotify_events();
        assert(g_num_fds == 2);

        /* IN_CREATE of an event-node name that cannot be opened → the
         * graceful-skip path (tracked set unchanged). */
        create_file(scratch, IMPOSSIBLE_NODE);
        handle_inotify_events();
        assert(g_num_fds == 2);

        /* Create a tracked node's file for real, then delete it →
         * IN_CREATE (add attempt → graceful skip) followed by
         * IN_DELETE → real end-to-end removal by name. */
        create_file(scratch, "event42");
        handle_inotify_events(); /* IN_CREATE → add attempt → graceful skip */
        assert(g_num_fds == 2);
        unlink_file(scratch, "event42");
        handle_inotify_events(); /* IN_DELETE → remove_device_by_name */
        assert(g_num_fds == 1);
        assert(find_device_index_by_name("event42") == -1);
        assert(find_device_index_by_name("event43") == 0);

        /* IN_MOVED_TO onto a tracked name → add is idempotent (no-op),
         * then IN_MOVED_FROM removes it end-to-end. */
        create_file(scratch, "staging2");
        rename_file(scratch, "staging2", "event43");
        handle_inotify_events(); /* IN_MOVED_TO → already tracked → no-op */
        assert(g_num_fds == 1);
        rename_file(scratch, "event43", "staging2"); /* IN_MOVED_FROM */
        handle_inotify_events();
        assert(g_num_fds == 0);

        /* Drain/cleanup: leftover scratch files removed via the watched
         * dir (also exercises more real events, all must be harmless). */
        unlink_file(scratch, "mouse0");
        unlink_file(scratch, "mice");
        unlink_file(scratch, IMPOSSIBLE_NODE);
        unlink_file(scratch, "staging2");
        handle_inotify_events();
        assert(g_num_fds == 0);

        g_inotify_fd = -1; /* detach before closing the watch fd */
        unwatch_scratch_dir(wfd);
        reset_tracked_devices();
    }

    /* ── 8. handle_inotify_events with no watch → safe no-op ───────────── */
    {
        g_inotify_fd = -1;
        handle_inotify_events(); /* must not touch anything, not crash */
        assert(g_num_fds == 0);
    }

    /* ── 9. Degrade path: setup_hotplug_watch never exits ──────────────── */
    /* On a machine WITHOUT /dev/input the watch setup fails and hotplug is
     * disabled (fd == -1) but the listener would keep running; on a machine
     * WITH /dev/input the watch succeeds (fd >= 0). Either way it must not
     * crash, and close_hotplug_watch must always reset the fd. */
    {
        setup_hotplug_watch();
        if (access("/dev/input", F_OK) != 0) {
            assert(g_inotify_fd == -1); /* degraded, not dead */
        }
        close_hotplug_watch();
        assert(g_inotify_fd == -1);
        /* A second close is a safe no-op. */
        close_hotplug_watch();
        assert(g_inotify_fd == -1);
    }

    /* ── Cleanup scratch dir (should be empty by now) ──────────────────── */
    assert(rmdir(scratch) == 0);

    printf("test_linux_key_listener_hotplug: ALL PASSED\n");
    return 0;
}
