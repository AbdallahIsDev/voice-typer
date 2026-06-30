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
 * SPDX-License-Identifier: project-wide
 * =============================================================================
 */

#define _GNU_SOURCE /* for strdup, strcasestr in glibc */
#include <dirent.h>
#include <errno.h>
#include <fcntl.h>
#include <linux/input.h>
#include <poll.h>
#include <signal.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <unistd.h>

#define MAX_DEVICES 64
#define EV_KEY_BITS_LEN ((KEY_MAX + 7) / 8)

/* ─── Wire protocol emitter ──────────────────────────────────────────────── */

static void emit(const char *line) {
    /* Use a single fwrite + fflush so the Python parent sees a complete line.
     * stdout is fully buffered when piped, so fflush is mandatory. */
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
static volatile sig_atomic_t g_should_exit = 0;

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

                const char *prefix = (ev.value == 1) ? "DOWN" : "UP";
                if (kn->is_modifier) {
                    emitf("MOD_%s:%s", prefix, kn->name);
                } else {
                    emitf("KEY_%s:%s", prefix, kn->name);
                }
            }
            /* EAGAIN is expected when O_NONBLOCK is set and we've drained */
        }
    }

    return 0;
}

/* ─── Entry point ───────────────────────────────────────────────────────── */

int main(int argc, char **argv) {
    if (argc < 2 || argv[1] == NULL || argv[1][0] == '\0') {
        emit("ERROR:Missing hotkey spec argument. Usage: linux-key-listener <hotkey-spec>");
        return 1;
    }

    /* Validate the spec up front so we fail fast on bad input. */
    int v = validate_hotkey_spec(argv[1]);
    if (v == 0) {
        emitf("ERROR:Invalid hotkey spec: %s", argv[1]);
        return 1;
    }
    if (v == -1) {
        emitf("ERROR:Invalid hotkey spec: %s (FN key not supported on Linux — firmware-only)", argv[1]);
        return 1;
    }

    /* Install signal handlers for clean shutdown. */
    struct sigaction sa;
    memset(&sa, 0, sizeof(sa));
    sa.sa_handler = on_signal;
    sigemptyset(&sa.sa_mask);
    sigaction(SIGINT, &sa, NULL);
    sigaction(SIGTERM, &sa, NULL);

    /* Set stdout to line-buffered (still helpful even though we fflush). */
    setvbuf(stdout, NULL, _IOLBF, 0);

    /* Open all keyboard devices. */
    if (discover_devices() < 0) {
        return 1;
    }

    /* Run the event loop until SIGINT/SIGTERM. */
    int rc = run_loop();

    /* Cleanup. */
    close_devices();
    return rc;
}
