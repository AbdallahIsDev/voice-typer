/* =============================================================================
 * Voice Typer — Linux native key listener
 *
 * Reads keyboard events from /dev/input/event* (evdev) and emits line-delimited
 * events on stdout for the Python parent process to match against the registered
 * hotkey. Works on both X11 and Wayland because evdev is below the display
 * server. Modeled on Freestyle's linux-key-listener.c (trimmed).
 *
 * Wire protocol (one event per line, newline-terminated):
 *   READY                          # emitted once after init succeeds
 *   KEY_DOWN:<Name>                # non-modifier key pressed
 *   KEY_UP:<Name>                  # non-modifier key released
 *   MOD_DOWN:<Name>                # modifier pressed (Ctrl, Shift, Alt, Super)
 *   MOD_UP:<Name>                  # modifier released
 *   ERROR:<message>                # fatal error, then exit(1)
 *
 * Limitation: evdev is read-only — we cannot suppress keystrokes on Linux.
 * The foreground app will still see the keystroke that triggers dictation.
 * (Same limitation as Freestyle's Linux backend.)
 *
 * Build:
 *   gcc -O2 -std=c99 linux-key-listener.c -o linux-key-listener
 *
 * SPDX-License-Identifier: MIT
 * =============================================================================
 */

#define _GNU_SOURCE /* for strdup, strcasestr in glibc */
#include <dirent.h>
#include <errno.h>
#include <fcntl.h>
#include <linux/input.h>
#include <poll.h>
#include <pthread.h>
#include <signal.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/time.h>
#include <time.h>
#include <unistd.h>

#define MAX_DEVICES 64
#define EV_KEY_BITS_LEN ((KEY_MAX + 7) / 8)

/* Wire protocol version reported via ``VERSION:<x.y.z>`` immediately
 * after READY. The Python side records this and the factory
 * compares it against the manifest's ``version`` field. */
#define NATIVE_BINARY_VERSION "1.0.0"

/* optional diagnostic log file. NULL when no --log-file was
 * passed (diagnostics go to stderr only, which the Python parent
 * merges into stdout via STDERR=STDOUT). */
static FILE *g_log_file = NULL;

/* Forward-declared here so the PING/PONG stdin reader thread can check
 * it. The full definition lives in the Global state section below. */
static volatile sig_atomic_t g_should_exit = 0;

/* ─── Diagnostic logger ────────────────────────────────────────── */

static void log_diag(const char *fmt, ...) {
    char ts[32];
    struct timeval tv;
    gettimeofday(&tv, NULL);
    struct tm tm_buf;
    gmtime_r(&tv.tv_sec, &tm_buf);
    strftime(ts, sizeof(ts), "%Y-%m-%dT%H:%M:%S", &tm_buf);
    char msg[512];
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(msg, sizeof(msg), fmt, ap);
    va_end(ap);
    char line[640];
    int n = snprintf(line, sizeof(line), "%s.%03ld [%d] %s\n",
                     ts, (long)(tv.tv_usec / 1000), (int)getpid(), msg);
    if (n > 0 && g_log_file != NULL) {
        fputs(line, g_log_file);
        fflush(g_log_file);
    }
    fputs(line, stderr);
    fflush(stderr);
}

/* ─── Wire protocol emitter ──────────────────────────────────────────────── */

static void emit(const char *line) {
    fputs(line, stdout);
    fputc('\n', stdout);
    fflush(stdout);
}

static void emitf(const char *fmt, ...) {
    char buf[256];
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(buf, sizeof(buf), fmt, ap);
    va_end(ap);
    emit(buf);
}

/* ─── PING/PONG stdin reader thread ────────────────────────────── */

/* Reads ``PING\n`` from stdin and emits ``PONG\n`` so the Python parent's
 * liveness watchdog can distinguish "alive and responsive" from "alive
 * but stuck". Without this thread the parent's PING writes would buffer
 * in the stdin pipe and we'd never see them. */
static void *stdin_reader_thread(void *arg) {
    (void)arg;
    char line[64];
    while (!g_should_exit) {
        if (fgets(line, sizeof(line), stdin) == NULL) {
            break;
        }
        size_t len = strlen(line);
        while (len > 0 && (line[len-1] == '\n' || line[len-1] == '\r')) {
            line[--len] = '\0';
        }
        if (strcmp(line, "PING") == 0) {
            emit("PONG");
        }
    }
    return NULL;
}

/* ─── Key code → name table ──────────────────────────────────────────────── */

typedef struct {
    int code;
    const char *name;
    int is_modifier; /* 1 if this is Ctrl/Shift/Alt/Super (emits MOD_DOWN/MOD_UP) */
} key_name_t;

static const key_name_t KEY_NAMES[] = {
    /* Modifiers — emit MOD_DOWN/MOD_UP instead of KEY_DOWN/KEY_UP */
    {KEY_LEFTCTRL,   "Ctrl",  1},
    {KEY_RIGHTCTRL,  "Ctrl",  1},
    {KEY_LEFTSHIFT,  "Shift", 1},
    {KEY_RIGHTSHIFT, "Shift", 1},
    {KEY_LEFTALT,    "Alt",   1},
    {KEY_RIGHTALT,   "Alt",   1},
    {KEY_LEFTMETA,   "Super", 1},
    {KEY_RIGHTMETA,  "Super", 1},

    /* Function keys F1..F24 (Linux only defines up to KEY_F24 in older
     * headers; if KEY_F24 is missing, the table simply omits the entry.) */
    {KEY_F1,  "F1",  0},  {KEY_F2,  "F2",  0},  {KEY_F3,  "F3",  0},
    {KEY_F4,  "F4",  0},  {KEY_F5,  "F5",  0},  {KEY_F6,  "F6",  0},
    {KEY_F7,  "F7",  0},  {KEY_F8,  "F8",  0},  {KEY_F9,  "F9",  0},
    {KEY_F10, "F10", 0},  {KEY_F11, "F11", 0},  {KEY_F12, "F12", 0},
#ifdef KEY_F13
    {KEY_F13, "F13", 0},  {KEY_F14, "F14", 0},  {KEY_F15, "F15", 0},
    {KEY_F16, "F16", 0},  {KEY_F17, "F17", 0},  {KEY_F18, "F18", 0},
    {KEY_F19, "F19", 0},  {KEY_F20, "F20", 0},  {KEY_F21, "F21", 0},
    {KEY_F22, "F22", 0},  {KEY_F23, "F23", 0},  {KEY_F24, "F24", 0},
#endif

    /* Top-row digits */
    {KEY_1, "1", 0}, {KEY_2, "2", 0}, {KEY_3, "3", 0}, {KEY_4, "4", 0},
    {KEY_5, "5", 0}, {KEY_6, "6", 0}, {KEY_7, "7", 0}, {KEY_8, "8", 0},
    {KEY_9, "9", 0}, {KEY_0, "0", 0},

    /* Letters */
    {KEY_Q, "Q", 0}, {KEY_W, "W", 0}, {KEY_E, "E", 0}, {KEY_R, "R", 0},
    {KEY_T, "T", 0}, {KEY_Y, "Y", 0}, {KEY_U, "U", 0}, {KEY_I, "I", 0},
    {KEY_O, "O", 0}, {KEY_P, "P", 0},
    {KEY_A, "A", 0}, {KEY_S, "S", 0}, {KEY_D, "D", 0}, {KEY_F, "F", 0},
    {KEY_G, "G", 0}, {KEY_H, "H", 0}, {KEY_J, "J", 0}, {KEY_K, "K", 0},
    {KEY_L, "L", 0},
    {KEY_Z, "Z", 0}, {KEY_X, "X", 0}, {KEY_C, "C", 0}, {KEY_V, "V", 0},
    {KEY_B, "B", 0}, {KEY_N, "N", 0}, {KEY_M, "M", 0},

    /* Editing & navigation */
    {KEY_ENTER,      "Enter",      0},
    {KEY_ESC,        "Esc",        0},
    {KEY_BACKSPACE,  "Backspace",  0},
    {KEY_TAB,        "Tab",        0},
    {KEY_SPACE,      "Space",      0},
    {KEY_MINUS,      "-",          0},
    {KEY_EQUAL,      "=",          0},
    {KEY_LEFTBRACE,  "[",          0},
    {KEY_RIGHTBRACE, "]",          0},
    {KEY_BACKSLASH,  "\\",         0},
    {KEY_SEMICOLON,  ";",          0},
    {KEY_APOSTROPHE, "'",          0},
    {KEY_GRAVE,      "`",          0},
    {KEY_COMMA,      ",",          0},
    {KEY_DOT,        ".",          0},
    {KEY_SLASH,      "/",          0},

    /* Editing block */
    {KEY_INSERT,    "Insert",     0},
    {KEY_DELETE,    "Delete",     0},
    {KEY_HOME,      "Home",       0},
    {KEY_END,       "End",        0},
    {KEY_PAGEUP,    "PageUp",     0},
    {KEY_PAGEDOWN,  "PageDown",   0},

    /* Arrows */
    {KEY_UP,    "Up",    0},
    {KEY_DOWN,  "Down",  0},
    {KEY_LEFT,  "Left",  0},
    {KEY_RIGHT, "Right", 0},

    /* Lock keys */
    {KEY_CAPSLOCK,   "CapsLock",    0},
    {KEY_NUMLOCK,    "NumLock",     0},
    {KEY_SCROLLLOCK, "ScrollLock",  0},
    {KEY_SYSRQ,      "PrintScreen", 0}, /* SysRq / PrintScreen share scancode */
    {KEY_PAUSE,      "Pause",       0},

    /* Numpad (Linux input-event-codes.h names: KEY_KPDOT not KEY_KPDECIMAL,
     * KEY_KPASTERISK not KEY_KPMULTIPLY, KEY_KPSLASH not KEY_KPDIVIDE) */
    {KEY_KP0,        "Num0",        0},
    {KEY_KP1,        "Num1",        0},
    {KEY_KP2,        "Num2",        0},
    {KEY_KP3,        "Num3",        0},
    {KEY_KP4,        "Num4",        0},
    {KEY_KP5,        "Num5",        0},
    {KEY_KP6,        "Num6",        0},
    {KEY_KP7,        "Num7",        0},
    {KEY_KP8,        "Num8",        0},
    {KEY_KP9,        "Num9",        0},
    {KEY_KPDOT,      "NumDecimal",  0},
    {KEY_KPPLUS,     "NumAdd",      0},
    {KEY_KPMINUS,    "NumSubtract", 0},
    {KEY_KPASTERISK, "NumMultiply", 0},
    {KEY_KPSLASH,    "NumDivide",   0},
    {KEY_KPENTER,    "NumEnter",    0},

    /* Media keys — not all keyboards emit these, but if they do we report them */
#ifdef KEY_PLAYPAUSE
    {KEY_PLAYPAUSE,       "MediaPlay", 0},
#endif
#ifdef KEY_STOPCD
    {KEY_STOPCD,          "MediaStop", 0},
#endif
#ifdef KEY_NEXTSONG
    {KEY_NEXTSONG,        "MediaNext", 0},
#endif
#ifdef KEY_PREVIOUSSONG
    {KEY_PREVIOUSSONG,    "MediaPrev", 0},
#endif

    {0, NULL, 0} /* sentinel */
};

static const key_name_t *lookup_key(int code) {
    for (const key_name_t *p = KEY_NAMES; p->name != NULL; p++) {
        if (p->code == code) {
            return p;
        }
    }
    return NULL;
}

/* ─── Global state ──────────────────────────────────────────────────────── */

static int g_fds[MAX_DEVICES];
static int g_num_fds = 0;
/* g_should_exit is forward-declared above (near the stdin reader thread
 * that uses it) so the PING/PONG thread can reference it. */

/* ─── Cross-device evdev deduplication ────────────────────────────────────
 *
 * On systems with multiple keyboard-like /dev/input/eventN devices (laptop
 * internal + USB dock, AT-translated + dock keyboard), the kernel broadcasts
 * the same physical keystroke to every open keyboard fd. Without dedup, each
 * press produces N KEY_DOWN:<Name> lines on stdout, and the Python matcher
 * fires the callback N times.
 *
 * The kernel stamps every duplicate of a single hardware event with the SAME
 * (tv_sec, tv_usec) timestamp, so we use that as a reliable dedup signal.
 * A 5 ms slack window (inclusive) tolerates drivers that re-stamp duplicates
 * at slightly different times, while remaining far below the ~80 ms
 * inter-keystroke interval for fast typists (so genuine double-taps are
 * never suppressed).
 */
#define DEDUP_WINDOW_MS 5
static struct input_event g_last_emitted_ev;
static int g_have_last_ev = 0;

static int is_duplicate_event(const struct input_event *ev) {
    if (!g_have_last_ev) return 0;
    if (ev->type != g_last_emitted_ev.type) return 0;
    if (ev->code != g_last_emitted_ev.code) return 0;
    if (ev->value != g_last_emitted_ev.value) return 0;
    /* Compare timestamps with a 5 ms slack window. Use long long to
     * avoid int overflow when computing the delta. */
    long long ev_ms = (long long)ev->time.tv_sec * 1000LL + (long long)ev->time.tv_usec / 1000LL;
    long long last_ms = (long long)g_last_emitted_ev.time.tv_sec * 1000LL + (long long)g_last_emitted_ev.time.tv_usec / 1000LL;
    long long delta = ev_ms - last_ms;
    if (delta < 0) delta = -delta;
    return delta <= DEDUP_WINDOW_MS;
}

static void remember_emitted_event(const struct input_event *ev) {
    g_last_emitted_ev = *ev;
    g_have_last_ev = 1;
}

/* ─── Signal handler ─────────────────────────────────────────────────────── */

static void on_signal(int sig) {
    (void)sig;
    g_should_exit = 1;
}

/* ─── Hotkey spec parsing (validation only — no suppression on Linux) ────── */

/* accepted tokens for validation. "fn" is rejected at parse time. */
static int is_valid_token(const char *t) {
    /* modifiers */
    if (!strcmp(t, "ctrl") || !strcmp(t, "control")) return 1;
    if (!strcmp(t, "shift")) return 1;
    if (!strcmp(t, "alt") || !strcmp(t, "alt_l") || !strcmp(t, "alt_r") ||
        !strcmp(t, "altgr") || !strcmp(t, "right_alt") || !strcmp(t, "ralt")) return 1;
    if (!strcmp(t, "cmd") || !strcmp(t, "win") || !strcmp(t, "super")) return 1;
    /* fn — explicitly rejected on Linux (firmware-only on most laptops) */
    if (!strcmp(t, "fn") || !strcmp(t, "globe")) return -1;
    /* special keys */
    if (!strcmp(t, "caps_lock") || !strcmp(t, "capslock")) return 1;
    if (!strcmp(t, "num_lock") || !strcmp(t, "numlock")) return 1;
    if (!strcmp(t, "scroll_lock") || !strcmp(t, "scrolllock")) return 1;
    if (!strcmp(t, "print_screen") || !strcmp(t, "printscreen")) return 1;
    if (!strcmp(t, "pause")) return 1;
    if (!strcmp(t, "esc") || !strcmp(t, "escape")) return 1;
    if (!strcmp(t, "space")) return 1;
    if (!strcmp(t, "enter") || !strcmp(t, "return")) return 1;
    if (!strcmp(t, "tab")) return 1;
    if (!strcmp(t, "backspace")) return 1;
    if (!strcmp(t, "insert")) return 1;
    if (!strcmp(t, "delete") || !strcmp(t, "del")) return 1;
    if (!strcmp(t, "home")) return 1;
    if (!strcmp(t, "end")) return 1;
    if (!strcmp(t, "page_up") || !strcmp(t, "pageup")) return 1;
    if (!strcmp(t, "page_down") || !strcmp(t, "pagedown")) return 1;
    /* arrows */
    if (!strcmp(t, "up") || !strcmp(t, "down") ||
        !strcmp(t, "left") || !strcmp(t, "right")) return 1;
    /* function keys f1..f24 */
    if (t[0] == 'f' && t[1] >= '1' && t[1] <= '9') {
        /* f1..f9 */
        if (t[2] == '\0') return 1;
        return 0;
    }
    if (t[0] == 'f' && t[1] == '1' && t[2] >= '0' && t[2] <= '9' && t[3] == '\0') {
        /* f10..f19 */
        return 1;
    }
    if (t[0] == 'f' && t[1] == '2' && t[2] >= '0' && t[2] <= '4' && t[3] == '\0') {
        /* f20..f24 */
        return 1;
    }
    /* single letter */
    if (strlen(t) == 1 && ((t[0] >= 'a' && t[0] <= 'z') ||
                           (t[0] >= '0' && t[0] <= '9'))) {
        return 1;
    }
    return 0;
}

static int validate_hotkey_spec(const char *spec) {
    /* Strip < > and split on + */
    char buf[256];
    strncpy(buf, spec, sizeof(buf) - 1);
    buf[sizeof(buf) - 1] = '\0';

    /* Strip < > brackets */
    char *p = buf;
    while (*p == '<' || *p == ' ' || *p == '\t') p++;
    char *end = p + strlen(p);
    while (end > p && (end[-1] == '>' || end[-1] == ' ' || end[-1] == '\t' ||
                       end[-1] == '\n' || end[-1] == '\r')) {
        end--;
        *end = '\0';
    }

    /* Empty spec */
    if (*p == '\0') {
        return 0;
    }

    /* Split on + */
    char *saveptr = NULL;
    char *tok = strtok_r(p, "+", &saveptr);
    int saw_fn = 0;
    int saw_any = 0;
    while (tok != NULL) {
        /* trim whitespace */
        while (*tok == ' ' || *tok == '\t') tok++;
        char *t_end = tok + strlen(tok);
        while (t_end > tok && (t_end[-1] == ' ' || t_end[-1] == '\t')) {
            t_end--;
            *t_end = '\0';
        }
        /* strip < > */
        char *inner = tok;
        if (*inner == '<') inner++;
        char *inner_end = inner + strlen(inner);
        if (inner_end > inner && inner_end[-1] == '>') {
            inner_end--;
            *inner_end = '\0';
        }

        int v = is_valid_token(inner);
        if (v == -1) {
            saw_fn = 1;
        } else if (v == 0) {
            return 0;
        }
        saw_any = 1;
        tok = strtok_r(NULL, "+", &saveptr);
    }

    if (!saw_any) {
        return 0;
    }
    if (saw_fn) {
        return -1; /* signal fn rejection to caller */
    }
    return 1;
}

/* ─── Device discovery ──────────────────────────────────────────────────── */

/* Test whether a /dev/input/eventN device reports EV_KEY events that look like
 * a keyboard (not just a mouse). Returns 1 if it's a keyboard-like device. */
static int is_keyboard_device(int fd) {
    unsigned char ev_bits[EV_MAX / 8 + 1] = {0};
    unsigned char key_bits[EV_KEY_BITS_LEN] = {0};

    /* Does it support EV_KEY at all? */
    if (ioctl(fd, EVIOCGBIT(0, sizeof(ev_bits)), ev_bits) < 0) {
        return 0;
    }
    if (!(ev_bits[EV_KEY / 8] & (1 << (EV_KEY % 8)))) {
        return 0;
    }

    /* Get the key bits */
    if (ioctl(fd, EVIOCGBIT(EV_KEY, sizeof(key_bits)), key_bits) < 0) {
        return 0;
    }

    /* Heuristic: a real keyboard reports at least KEY_A, KEY_SPACE, and
     * KEY_ENTER. This filters out mice (which only report BTN_LEFT etc.)
     * and other button-only devices. */
    if (!(key_bits[KEY_A / 8] & (1 << (KEY_A % 8)))) return 0;
    if (!(key_bits[KEY_SPACE / 8] & (1 << (KEY_SPACE % 8)))) return 0;
    if (!(key_bits[KEY_ENTER / 8] & (1 << (KEY_ENTER % 8)))) return 0;
    return 1;
}

static int discover_devices(void) {
    DIR *dir = opendir("/dev/input");
    if (dir == NULL) {
        emitf("ERROR:Cannot open /dev/input: %s", strerror(errno));
        if (errno == EACCES) {
            emit("ERROR:Permission denied. Add yourself to the 'input' group: sudo usermod -aG input $USER, then log out and back in.");
        }
        return -1;
    }

    struct dirent *ent;
    while ((ent = readdir(dir)) != NULL) {
        if (strncmp(ent->d_name, "event", 5) != 0) continue;
        if (g_num_fds >= MAX_DEVICES) break;

        char path[320]; /* /dev/input/ + d_name (up to 256) + NUL */
        snprintf(path, sizeof(path), "/dev/input/%s", ent->d_name);
        int fd = open(path, O_RDONLY | O_NONBLOCK);
        if (fd < 0) {
            /* Skip silently — some devices may be unavailable */
            continue;
        }
        if (!is_keyboard_device(fd)) {
            close(fd);
            continue;
        }
        g_fds[g_num_fds++] = fd;
    }
    closedir(dir);

    if (g_num_fds == 0) {
        emit("ERROR:No keyboard devices found in /dev/input/event*. Are you in the 'input' group?");
        return -1;
    }
    return 0;
}

static void close_devices(void) {
    for (int i = 0; i < g_num_fds; i++) {
        if (g_fds[i] >= 0) {
            close(g_fds[i]);
            g_fds[i] = -1;
        }
    }
    g_num_fds = 0;
}

/* ─── Main loop ─────────────────────────────────────────────────────────── */

static int run_loop(void) {
    struct pollfd pfds[MAX_DEVICES];
    for (int i = 0; i < g_num_fds; i++) {
        pfds[i].fd = g_fds[i];
        pfds[i].events = POLLIN;
        pfds[i].revents = 0;
    }

    /* Emit READY after devices are open and we're ready to read events. */
    emit("READY");
    /* immediately announce our wire-protocol version so the
     * Python side can compare against the manifest's ``version`` field. */
    emitf("VERSION:%s", NATIVE_BINARY_VERSION);
    log_diag("READY emitted; version=%s; devices=%d", NATIVE_BINARY_VERSION, g_num_fds);

    while (!g_should_exit) {
        int n = poll(pfds, (nfds_t)g_num_fds, 500 /* ms */);
        if (n < 0) {
            if (errno == EINTR) continue;
            emitf("ERROR:poll() failed: %s", strerror(errno));
            return 1;
        }
        if (n == 0) continue; /* timeout — check g_should_exit */

        for (int i = 0; i < g_num_fds; i++) {
            if (!(pfds[i].revents & POLLIN)) continue;

            /* Drain all available events from this device. evdev events are
             * 24 bytes on most architectures (struct input_event). */
            struct input_event ev;
            while (read(pfds[i].fd, &ev, sizeof(ev)) == (ssize_t)sizeof(ev)) {
                if (ev.type != EV_KEY) continue;
                if (ev.value == 2) continue; /* autorepeat — ignore */

                const key_name_t *kn = lookup_key((int)ev.code);
                if (kn == NULL) continue; /* unmapped key — skip silently */

                /* Cross-device dedup: suppress duplicate broadcasts of the
                 * same hardware event arriving on multiple open keyboard fds. */
                if (is_duplicate_event(&ev)) continue;

                const char *prefix = (ev.value == 1) ? "DOWN" : "UP";
                if (kn->is_modifier) {
                    emitf("MOD_%s:%s", prefix, kn->name);
                } else {
                    emitf("KEY_%s:%s", prefix, kn->name);
                }
                remember_emitted_event(&ev);
            }
            /* EAGAIN is expected when O_NONBLOCK is set and we've drained */
        }
    }

    return 0;
}

/* ─── Entry point ───────────────────────────────────────────────────────── */

int main(int argc, char **argv) {
    if (argc < 2 || argv[1] == NULL || argv[1][0] == '\0') {
        emit("ERROR:Missing hotkey spec argument. Usage: linux-key-listener <hotkey-spec> [--log-file <path>]");
        return 1;
    }

    /* parse optional ``--log-file <path>`` argument. Also accept
     * a bare positional argv[2] as the log path for forward compatibility. */
    const char *log_file_path = NULL;
    for (int i = 2; i < argc; i++) {
        if (strcmp(argv[i], "--log-file") == 0 && i + 1 < argc) {
            log_file_path = argv[++i];
        } else if (argv[i][0] != '-' && log_file_path == NULL) {
            log_file_path = argv[i];
        }
    }
    if (log_file_path != NULL && log_file_path[0] != '\0') {
        g_log_file = fopen(log_file_path, "a");
        if (g_log_file == NULL) {
            fputs("WARN:Failed to open --log-file: ", stderr);
            fputs(strerror(errno), stderr);
            fputc('\n', stderr);
            fflush(stderr);
        }
    }
    log_diag("linux-key-listener starting; spec=%s; log_file=%s",
             argv[1], log_file_path ? log_file_path : "<none>");

    /* Validate the spec up front so we fail fast on bad input. */
    int v = validate_hotkey_spec(argv[1]);
    if (v == 0) {
        emitf("ERROR:Invalid hotkey spec: %s", argv[1]);
        log_diag("ERROR: invalid hotkey spec: %s", argv[1]);
        return 1;
    }
    if (v == -1) {
        emitf("ERROR:Invalid hotkey spec: %s (FN key not supported on Linux — firmware-only)", argv[1]);
        log_diag("ERROR: invalid hotkey spec (FN rejected): %s", argv[1]);
        return 1;
    }
    log_diag("hotkey spec validated");

    /* Install signal handlers for clean shutdown. */
    struct sigaction sa;
    memset(&sa, 0, sizeof(sa));
    sa.sa_handler = on_signal;
    sigemptyset(&sa.sa_mask);
    sigaction(SIGINT, &sa, NULL);
    sigaction(SIGTERM, &sa, NULL);

    /* Set stdout to line-buffered (still helpful even though we fflush). */
    setvbuf(stdout, NULL, _IOLBF, 0);

    /* start the stdin reader thread so we can respond to PING
     * with PONG. Detached — exits on EOF or g_should_exit. */
    pthread_t stdin_tid;
    if (pthread_create(&stdin_tid, NULL, stdin_reader_thread, NULL) != 0) {
        log_diag("WARN: failed to start stdin reader thread; PING/PONG disabled");
    } else {
        pthread_detach(stdin_tid);
        log_diag("stdin reader thread started (PING/PONG enabled)");
    }

    /* Open all keyboard devices. */
    if (discover_devices() < 0) {
        log_diag("ERROR: discover_devices failed");
        return 1;
    }
    log_diag("keyboard devices opened: %d", g_num_fds);

    /* Run the event loop until SIGINT/SIGTERM. */
    int rc = run_loop();
    log_diag("event loop exited; rc=%d", rc);

    /* Cleanup. */
    close_devices();
    if (g_log_file != NULL) {
        fclose(g_log_file);
        g_log_file = NULL;
    }
    return rc;
}
