/* =============================================================================
 * Voice Typer — Linux native key-listener dedup unit test (C-level)
 *
 * Compiles the production listener source (``linux-key-listener.c``) INTO this
 * test translation unit via ``#include`` so the file-local static helpers
 * ``is_duplicate_event`` and ``remember_emitted_event`` become reachable from
 * the test. The listener's own ``main`` is renamed out of the way via a
 * ``#define main listener_main`` macro (just before the ``#include``) so this
 * file owns the real entry point.
 *
 * Build (mirror of what the Python integration test runs):
 *   gcc -O2 -std=c99 -Wall -Wextra -Wno-unused-function \
 *       tests/c/test_linux_key_listener_dedup.c \
 *       -o /tmp/test_dedup
 *
 * The dedup logic is the mitigation for the multi-keyboard double-fire: on a
 * system with overlapping ``/dev/input/event*`` keyboard nodes (laptop
 * internal + USB dock, AT-translated + dock keyboard, etc.), the same physical
 * keystroke is broadcast to every open keyboard fd. The kernel stamps every
 * duplicate with the SAME (tv_sec, tv_usec) timestamp; we suppress any
 * subsequent event whose (code, value, time) matches the prior emitted event
 * within a 5 ms window.
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
#include <stdio.h>
#include <string.h>

/* Pull the production source into this translation unit so its file-local
 * static helpers (is_duplicate_event, remember_emitted_event) are reachable.
 * The listener's own ``main`` is renamed to ``listener_main`` via a macro so
 * this file is free to define its own entry point below. */
#define main listener_main
#include "../../voice_typer/server/native/linux-key-listener.c"
#undef main

/* Helper: build a synthetic EV_KEY event. */
static struct input_event make_ev(int code, int value, long sec, long usec) {
    struct input_event ev;
    memset(&ev, 0, sizeof(ev));
    ev.type = EV_KEY;
    ev.code = (unsigned short)code;
    ev.value = value;
    ev.time.tv_sec = sec;
    ev.time.tv_usec = usec;
    return ev;
}

int main(void) {
    /* ── Setup: no prior event ──────────────────────────────────────────── */
    /* is_duplicate_event must return 0 when no event has been remembered. */
    {
        struct input_event ev = make_ev(KEY_A, 1, 1000, 0);
        assert(is_duplicate_event(&ev) == 0);
    }

    /* ── Exact-match duplicate (same code, value, time) ─────────────────── */
    /* The most common case: kernel broadcasts the SAME timestamped event to
     * every keyboard fd. After remembering the first emit, the second must
     * be flagged as a duplicate. */
    {
        struct input_event ev = make_ev(KEY_A, 1, 1000, 0);
        assert(is_duplicate_event(&ev) == 0);
        remember_emitted_event(&ev);

        struct input_event dup = make_ev(KEY_A, 1, 1000, 0);
        assert(is_duplicate_event(&dup) == 1);
    }

    /* ── Within 5 ms window — duplicate ─────────────────────────────────── */
    /* Some drivers re-stamp duplicates at slightly different times; the 5 ms
     * slack tolerates this. */
    {
        struct input_event ev = make_ev(KEY_SPACE, 1, 2000, 0);
        remember_emitted_event(&ev);

        struct input_event dup = make_ev(KEY_SPACE, 1, 2000, 4000); /* +4 ms */
        assert(is_duplicate_event(&dup) == 1);
    }

    /* ── Exactly 5 ms — duplicate (inclusive window) ────────────────────── */
    {
        struct input_event ev = make_ev(KEY_ENTER, 1, 3000, 0);
        remember_emitted_event(&ev);

        struct input_event dup = make_ev(KEY_ENTER, 1, 3000, 5000); /* +5 ms */
        assert(is_duplicate_event(&dup) == 1);
    }

    /* ── Beyond 5 ms — NOT a duplicate (genuine new press) ──────────────── */
    /* 6 ms after the prior event: the dedup window has closed, so this is
     * treated as a fresh key press. The 5 ms threshold is well below the
     * ~80 ms inter-keystroke interval for fast typists, so this only fires
     * for genuinely-far-apart events. */
    {
        struct input_event ev = make_ev(KEY_A, 1, 4000, 0);
        remember_emitted_event(&ev);

        struct input_event next = make_ev(KEY_A, 1, 4000, 6000); /* +6 ms */
        assert(is_duplicate_event(&next) == 0);
    }

    /* ── Different code — never a duplicate ─────────────────────────────── */
    {
        struct input_event ev = make_ev(KEY_A, 1, 5000, 0);
        remember_emitted_event(&ev);

        struct input_event other = make_ev(KEY_B, 1, 5000, 0);
        assert(is_duplicate_event(&other) == 0);
    }

    /* ── Different value (key-up vs key-down) — not a duplicate ─────────── */
    {
        struct input_event down = make_ev(KEY_A, 1, 6000, 0);
        remember_emitted_event(&down);

        struct input_event up = make_ev(KEY_A, 0, 6000, 0);
        assert(is_duplicate_event(&up) == 0);
    }

    /* ── Different second timestamp — not a duplicate ───────────────────── */
    /* A press more than 1 s after the prior one must never be suppressed. */
    {
        struct input_event ev = make_ev(KEY_A, 1, 7000, 0);
        remember_emitted_event(&ev);

        struct input_event later = make_ev(KEY_A, 1, 8000, 0); /* +1000 ms */
        assert(is_duplicate_event(&later) == 0);
    }

    /* ── Modifier events are deduped too ────────────────────────────────── */
    /* Cross-device duplicates of MOD_DOWN:Ctrl must also collapse. */
    {
        struct input_event mod = make_ev(KEY_LEFTCTRL, 1, 9000, 0);
        assert(is_duplicate_event(&mod) == 0);
        remember_emitted_event(&mod);

        struct input_event mod_dup = make_ev(KEY_LEFTCTRL, 1, 9000, 0);
        assert(is_duplicate_event(&mod_dup) == 1);
    }

    /* ── remember_emitted_event overwrites the prior state ──────────────── */
    /* After emitting a fresh event, only events matching the NEW state are
     * duplicates; events matching the OLD state are not. */
    {
        struct input_event a = make_ev(KEY_A, 1, 10000, 0);
        remember_emitted_event(&a);

        struct input_event b = make_ev(KEY_B, 1, 10000, 0);
        assert(is_duplicate_event(&b) == 0);
        remember_emitted_event(&b);

        /* An A-event now (matching the OLD state) is no longer a duplicate
         * of the current state (which is B), so it is NOT suppressed — even
         * though its timestamp is within the 5 ms window. */
        struct input_event a_again = make_ev(KEY_A, 1, 10000, 2000); /* +2 ms */
        assert(is_duplicate_event(&a_again) == 0);
    }

    /* ── Negative timestamp delta is tolerated (defensive) ──────────────── */
    /* Kernel input events should always have monotonic timestamps, but the
     * dedup must not misfire if a duplicate arrives with a slightly earlier
     * timestamp (e.g. due to driver re-stamping). The absolute value of the
     * delta is used. */
    {
        struct input_event ev = make_ev(KEY_A, 1, 11000, 5000);
        remember_emitted_event(&ev);

        struct input_event earlier = make_ev(KEY_A, 1, 11000, 2000); /* -3 ms */
        assert(is_duplicate_event(&earlier) == 1);
    }

    printf("test_linux_key_listener_dedup: ALL PASSED\n");
    return 0;
}
