/* =============================================================================
 * Voice Typer — Windows native key listener
 *
 * Emits line-delimited key events on stdout for the Python parent process to
 * match against the registered hotkey. Modeled on Freestyle's
 * windows-key-listener.c (trimmed). Uses a single Win32 low-level keyboard
 * hook (WH_KEYBOARD_LL) installed from the main thread, which also runs a
 * standard Win32 message pump.
 *
 * Why WH_KEYBOARD_LL (not WH_KEYBOARD, not RegisterHotKey, not
 * GetAsyncKeyState polling)?
 *   - WH_KEYBOARD_LL is an *out-of-process* hook: the callback lives in OUR
 *     binary's address space and is dispatched by the OS on the thread that
 *     called SetWindowsHookEx. No DLL is injected into other processes, so
 *     we don't need a separate hook DLL (and we don't need 32/64-bit variants
 *     of that DLL to match every target process).
 *   - It gives us the ability to *suppress* keystrokes (return non-zero from
 *     the callback) so the foreground app never sees them — e.g. swallow
 *     CapsLock so the OS doesn't toggle caps state.
 *   - It's event-driven, so the binary sits idle (one thread, blocked in
 *     GetMessage) until a key arrives — much lower CPU than 60 Hz polling.
 *
 * Wire protocol (one event per line, newline-terminated):
 *   READY                  # emitted once after init succeeds
 *   KEY_DOWN:<Name>        # non-modifier key pressed
 *   KEY_UP:<Name>          # non-modifier key released
 *   MOD_DOWN:<Name>        # modifier pressed (Ctrl, Shift, Alt, Win)
 *   MOD_UP:<Name>          # modifier released
 *   ERROR:<message>        # fatal error, then exit(1)
 *
 * FN_DOWN / FN_UP are NOT emitted on Windows — the Fn key is firmware-only on
 * Windows keyboards and never reaches the OS as a keystroke. The "fn" token in
 * argv[1] is rejected at parse time.
 *
 * Build (MSVC):
 *   cl.exe /O2 windows-key-listener.c /link user32.lib /out:windows-key-listener.exe
 * Build (MinGW):
 *   gcc -O2 windows-key-listener.c -o windows-key-listener.exe -luser32
 *
 * SPDX-License-Identifier: MIT
 * =============================================================================
 */

/* Vista+ (0x0600) is required for WH_KEYBOARD_LL. Must be defined before
 * any system header is included. */
#ifndef _WIN32_WINNT
#define _WIN32_WINNT 0x0600
#endif

/* Silence MSVC deprecation warnings for strncpy / strtok / etc. These are
 * still standard C99 and work fine on both MSVC and MinGW. */
#define _CRT_SECURE_NO_WARNINGS

#include <windows.h>
#include <stdio.h>
#include <string.h>
#include <stdlib.h>
#include <ctype.h>
#include <process.h>  /* _beginthreadex */

/* Wire protocol version reported via ``VERSION:<x.y.z>`` immediately
 * after READY. The Python side records this and the factory
 * compares it against the manifest's ``version`` field. */
#define NATIVE_BINARY_VERSION "1.0.0"

/* optional diagnostic log file. NULL when no --log-file was
 * passed (diagnostics go to stderr only). */
static FILE* g_diag_log = NULL;

/* stdout writes from the stdin-reader thread and the hook
 * callback must be serialized. The hook callback runs on the main
 * thread during the GetMessage pump; the stdin reader runs on its
 * own thread. Initialize in main(). */
static CRITICAL_SECTION g_emit_lock;
static int g_emit_lock_inited = 0;

/* ===========================================================================
 * Globals
 * =========================================================================== */

static HHOOK g_hook = NULL;   /* the low-level keyboard hook handle */
static FILE* g_out   = NULL;  /* stdout, captured for emit() */

/* signal the stdin reader thread to exit so we don't leak a
 * thread hanging on ReadFile(stdin) when the parent closes the pipe. */
static volatile LONG g_should_exit = 0;
/* Main thread's id, captured at startup: the stdin reader thread posts
 * WM_QUIT to it on stdin EOF (see stdin_reader_thread). */
static DWORD g_main_thread_id = 0;

/* When we swallow a keyDown, we remember its VK so the matching keyUp is
 * also swallowed — otherwise the foreground app sees an orphan keyUp
 * (keydown suppressed, keyup delivered), which can confuse it. */
static int g_suppressed_vk = 0;

/* ===========================================================================
 * Parsed hotkey spec
 *
 * The binary does NOT do hotkey matching — Python does. We only parse the
 * spec so we can (a) reject invalid specs early with ERROR, and (b) know
 * which keystrokes to suppress in the hook callback so they don't reach the
 * foreground app.
 * =========================================================================== */

typedef struct {
    /* Normalized modifier names present in the spec. Each is one of
     * "Ctrl", "Shift", "Alt", "Win" (no duplicates). */
    char modifiers[8][16];
    int  modifier_count;
    /* Normalized main-key wire name (e.g. "V", "F2", "Space", "CapsLock"),
     * or empty string for modifier-only hotkeys. */
    char main_key[32];
    /* True if the spec is exactly "<caps_lock>" alone. */
    int is_caps_lock_only;
    /* True if the spec has no main key (e.g. "<alt>", "<ctrl>+<shift>"). */
    int is_modifier_only;
    /* Original argv[1] string, kept for error messages. */
    char spec[256];
} HotkeySpec;

static HotkeySpec g_spec;  /* parsed argv[1] — only mutated at startup */

/* ===========================================================================
 * VK -> wire-name tables
 * =========================================================================== */

/* Non-modifier virtual-key codes -> wire-protocol names.
 *
 * Note: VK_RETURN (0x0D) is shared between the main Enter key and the numpad
 * Enter key. They are disambiguated at event time via the LLKHF_EXTENDED flag
 * in KBDLLHOOKSTRUCT.flags — see name_for_event(). */
static const struct { int vk; const char* name; } VK_NAMES[] = {
    /* Editing / navigation */
    { 0x08, "Backspace" },
    { 0x09, "Tab" },
    { 0x0D, "Enter" },          /* main Enter; numpad Enter resolved at event time */
    { 0x1B, "Esc" },
    { 0x20, "Space" },
    { 0x21, "PageUp" },
    { 0x22, "PageDown" },
    { 0x23, "End" },
    { 0x24, "Home" },
    { 0x25, "Left" },
    { 0x26, "Up" },
    { 0x27, "Right" },
    { 0x28, "Down" },
    { 0x2D, "Insert" },
    { 0x2E, "Delete" },
    /* Top-row digits */
    { 0x30, "0" }, { 0x31, "1" }, { 0x32, "2" }, { 0x33, "3" }, { 0x34, "4" },
    { 0x35, "5" }, { 0x36, "6" }, { 0x37, "7" }, { 0x38, "8" }, { 0x39, "9" },
    /* Letters A–Z (VK codes 0x41..0x5A map directly to uppercase ASCII) */
    { 0x41, "A" }, { 0x42, "B" }, { 0x43, "C" }, { 0x44, "D" }, { 0x45, "E" },
    { 0x46, "F" }, { 0x47, "G" }, { 0x48, "H" }, { 0x49, "I" }, { 0x4A, "J" },
    { 0x4B, "K" }, { 0x4C, "L" }, { 0x4D, "M" }, { 0x4E, "N" }, { 0x4F, "O" },
    { 0x50, "P" }, { 0x51, "Q" }, { 0x52, "R" }, { 0x53, "S" }, { 0x54, "T" },
    { 0x55, "U" }, { 0x56, "V" }, { 0x57, "W" }, { 0x58, "X" }, { 0x59, "Y" },
    { 0x5A, "Z" },
    /* Function keys F1..F24 (VK_F1=0x70 .. VK_F24=0x87) */
    { 0x70, "F1" },  { 0x71, "F2" },  { 0x72, "F3" },  { 0x73, "F4" },
    { 0x74, "F5" },  { 0x75, "F6" },  { 0x76, "F7" },  { 0x77, "F8" },
    { 0x78, "F9" },  { 0x79, "F10" }, { 0x7A, "F11" }, { 0x7B, "F12" },
    { 0x7C, "F13" }, { 0x7D, "F14" }, { 0x7E, "F15" }, { 0x7F, "F16" },
    { 0x80, "F17" }, { 0x81, "F18" }, { 0x82, "F19" }, { 0x83, "F20" },
    { 0x84, "F21" }, { 0x85, "F22" }, { 0x86, "F23" }, { 0x87, "F24" },
    /* Locks / misc */
    { 0x14, "CapsLock" },
    { 0x90, "NumLock" },
    { 0x91, "ScrollLock" },
    { 0x2C, "PrintScreen" },
    { 0x13, "Pause" },
    /* Numpad digits & operators */
    { 0x60, "Num0" }, { 0x61, "Num1" }, { 0x62, "Num2" }, { 0x63, "Num3" },
    { 0x64, "Num4" }, { 0x65, "Num5" }, { 0x66, "Num6" }, { 0x67, "Num7" },
    { 0x68, "Num8" }, { 0x69, "Num9" },
    { 0x6E, "NumDecimal" },
    { 0x6B, "NumAdd" },
    { 0x6D, "NumSubtract" },
    { 0x6A, "NumMultiply" },
    { 0x6F, "NumDivide" },
    /* Media keys */
    { 0xB0, "MediaNext" },
    { 0xB1, "MediaPrev" },
    { 0xB2, "MediaStop" },
    { 0xB3, "MediaPlay" },
    { 0,    NULL }
};

/* Modifier VK map. Both left and right sides of each modifier map to the same
 * wire name (we do NOT distinguish L/R in the wire protocol).
 *   VK_LSHIFT (0xA0) / VK_RSHIFT (0xA1)   → "Shift"
 *   VK_LCONTROL (0xA2) / VK_RCONTROL (0xA3) → "Ctrl"
 *   VK_LMENU (0xA4) / VK_RMENU (0xA5)     → "Alt"  (RMENU is AltGr; reported
 *                                                       as Alt for simplicity)
 *   VK_LWIN (0x5B) / VK_RWIN (0x5C)       → "Win"  (NOT "Cmd" on Windows) */
static const struct { int vk; const char* name; } MOD_VK_NAMES[] = {
    { 0xA0, "Shift" }, { 0xA1, "Shift" },
    { 0xA2, "Ctrl"  }, { 0xA3, "Ctrl"  },
    { 0xA4, "Alt"   }, { 0xA5, "Alt"   },
    { 0x5B, "Win"   }, { 0x5C, "Win"   },
    { 0,    NULL }
};

/* Returns the wire name for a non-modifier VK code, or NULL if unrecognized. */
static const char* name_for_vk(int vk) {
    int i;
    for (i = 0; VK_NAMES[i].name != NULL; i++) {
        if (VK_NAMES[i].vk == vk) return VK_NAMES[i].name;
    }
    return NULL;
}

/* Returns the modifier wire name for a modifier VK code, or NULL. */
static const char* mod_name_for_vk(int vk) {
    int i;
    for (i = 0; MOD_VK_NAMES[i].name != NULL; i++) {
        if (MOD_VK_NAMES[i].vk == vk) return MOD_VK_NAMES[i].name;
    }
    return NULL;
}

/* Resolve a (vk, flags) pair from KBDLLHOOKSTRUCT to its wire name.
 *
 * VK_RETURN (0x0D) is the only VK shared by two physical keys (main Enter and
 * numpad Enter). The main Enter key sends the LLKHF_EXTENDED flag; the numpad
 * Enter key does not. Disambiguate here. */
static const char* name_for_event(int vk, DWORD flags) {
    if (vk == VK_RETURN) {
        return (flags & LLKHF_EXTENDED) ? "Enter" : "NumEnter";
    }
    return name_for_vk(vk);
}

/* ===========================================================================
 * Wire-protocol emitter
 *
 * stdout on Windows is fully buffered when piped (the parent Python process
 * reads from a pipe). We both disable buffering via setvbuf() in main() AND
 * fflush() after every line — belt and suspenders. All emit() calls happen
 * on the hook-installing thread (the OS dispatches the LowLevelKeyboardProc
 * callback synchronously during our GetMessage() pump), so no locking is
 * needed.
 * =========================================================================== */

static void emit(const char* line) {
    if (g_emit_lock_inited) EnterCriticalSection(&g_emit_lock);
    fputs(line, g_out);
    fputc('\n', g_out);
    fflush(g_out);
    if (g_emit_lock_inited) LeaveCriticalSection(&g_emit_lock);
}

/* write a timestamped diagnostic line to g_diag_log (if set)
 * AND echo to stderr. Mirrors the Linux log_diag helper. */
static void log_diag(const char* fmt, ...) {
    SYSTEMTIME st;
    GetSystemTime(&st);
    char msg[512];
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(msg, sizeof(msg), fmt, ap);
    va_end(ap);
    char line[640];
    int n = snprintf(line, sizeof(line),
                     "%04d-%02d-%02dT%02d:%02d:%02d.%03d [%lu] %s\n",
                     st.wYear, st.wMonth, st.wDay,
                     st.wHour, st.wMinute, st.wSecond, st.wMilliseconds,
                     GetCurrentProcessId(), msg);
    if (n > 0 && g_diag_log != NULL) {
        if (g_emit_lock_inited) EnterCriticalSection(&g_emit_lock);
        fputs(line, g_diag_log);
        fflush(g_diag_log);
        if (g_emit_lock_inited) LeaveCriticalSection(&g_emit_lock);
    }
    fputs(line, stderr);
    fflush(stderr);
}

/* ===========================================================================
 * PING/PONG stdin reader thread
 *
 * The Python parent writes ``PING\n`` to our stdin every 30s as a
 * liveness check. We respond with ``PONG\n`` so the parent can tell
 * "alive and responsive" from "alive but stuck in a tight loop".
 * =========================================================================== */
static unsigned __stdcall stdin_reader_thread(void* arg) {
    (void)arg;
    char line[64];
    while (!g_should_exit) {
        if (fgets(line, sizeof(line), stdin) == NULL) {
            /* stdin EOF — the Python parent is gone (crash, force-kill,
             * power loss). Without this branch the process would linger
             * forever holding the low-level keyboard hook after the app
             * died ("keys feel dead" to other apps). Signal the exit flag
             * and wake the main thread's message pump with WM_QUIT so the
             * main-thread cleanup path unhooks and exits. The hook itself
             * is NOT unhooked here — only the installing thread may run
             * the cleanup path safely. */
            InterlockedExchange(&g_should_exit, 1);
            PostThreadMessage(g_main_thread_id, WM_QUIT, 0, 0);
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
    return 0;
}

/* ===========================================================================
 * Hotkey spec parsing
 *
 * Accepts pynput-style specs such as:
 *   "<caps_lock>"          — single non-modifier key
 *   "<alt>"                — single modifier
 *   "<f2>"                 — single function key
 *   "<ctrl>+<alt>+v"       — modifier combo + main key
 *
 * Tokens may be wrapped in <...> or bare; whitespace is trimmed; tokens are
 * matched case-insensitively. The "fn" token is rejected on Windows.
 * =========================================================================== */

/* in-place lowercase */
static void to_lower(char* s) {
    for (; *s; s++) *s = (char)tolower((unsigned char)*s);
}

/* trim leading/trailing whitespace in place */
static void trim(char* s) {
    char* p = s;
    while (*p == ' ' || *p == '\t' || *p == '\n' || *p == '\r') p++;
    if (p != s) memmove(s, p, strlen(p) + 1);
    size_t n = strlen(s);
    while (n > 0 && (s[n-1] == ' ' || s[n-1] == '\t'
                  || s[n-1] == '\n' || s[n-1] == '\r')) {
        s[--n] = '\0';
    }
}

/* strip < > brackets if both present */
static void strip_brackets(char* s) {
    size_t n = strlen(s);
    if (n >= 2 && s[0] == '<' && s[n-1] == '>') {
        memmove(s, s + 1, n - 2);
        s[n - 2] = '\0';
    }
}

/* Map of pynput-style tokens -> wire-protocol key names. */
static const struct { const char* in; const char* out; } KEY_NAME_MAP[] = {
    /* Function keys F1..F24 */
    { "f1", "F1" },  { "f2", "F2" },  { "f3", "F3" },  { "f4", "F4" },
    { "f5", "F5" },  { "f6", "F6" },  { "f7", "F7" },  { "f8", "F8" },
    { "f9", "F9" },  { "f10", "F10" }, { "f11", "F11" }, { "f12", "F12" },
    { "f13", "F13" }, { "f14", "F14" }, { "f15", "F15" }, { "f16", "F16" },
    { "f17", "F17" }, { "f18", "F18" }, { "f19", "F19" }, { "f20", "F20" },
    { "f21", "F21" }, { "f22", "F22" }, { "f23", "F23" }, { "f24", "F24" },
    /* Editing / navigation */
    { "space", "Space" }, { "enter", "Enter" }, { "return", "Enter" },
    { "tab", "Tab" }, { "esc", "Esc" }, { "escape", "Esc" },
    { "backspace", "Backspace" },
    { "insert", "Insert" }, { "delete", "Delete" },
    { "home", "Home" }, { "end", "End" },
    { "page_up", "PageUp" }, { "pageup", "PageUp" },
    { "page_down", "PageDown" }, { "pagedown", "PageDown" },
    /* Locks / misc */
    { "caps_lock", "CapsLock" }, { "capslock", "CapsLock" },
    { "num_lock", "NumLock" },   { "numlock", "NumLock" },
    { "scroll_lock", "ScrollLock" }, { "scrolllock", "ScrollLock" },
    { "print_screen", "PrintScreen" }, { "printscreen", "PrintScreen" },
    { "pause", "Pause" },
    /* Arrows */
    { "up", "Up" }, { "down", "Down" },
    { "left", "Left" }, { "right", "Right" },
    { NULL, NULL }
};

/* Normalize a pynput-style token to its wire-protocol key name.
 * Returns 1 on success (writes to out), 0 on failure. */
static int normalize_key_name(const char* tok, char* out, size_t out_size) {
    /* Single ASCII letter -> uppercase */
    if (strlen(tok) == 1 && isalpha((unsigned char)tok[0])) {
        out[0] = (char)toupper((unsigned char)tok[0]);
        out[1] = '\0';
        return 1;
    }
    /* Single ASCII digit -> preserved */
    if (strlen(tok) == 1 && isdigit((unsigned char)tok[0])) {
        out[0] = tok[0];
        out[1] = '\0';
        return 1;
    }
    /* Named keys (f1, space, caps_lock, ...) */
    int i;
    for (i = 0; KEY_NAME_MAP[i].in != NULL; i++) {
        if (strcmp(tok, KEY_NAME_MAP[i].in) == 0) {
            strncpy(out, KEY_NAME_MAP[i].out, out_size - 1);
            out[out_size - 1] = '\0';
            return 1;
        }
    }
    return 0;
}

/* Normalize a modifier token. Returns 1 on success, 0 on failure.
 * The "fn" token is NOT handled here — the caller rejects it before calling. */
static int normalize_modifier(const char* tok, char* out, size_t out_size) {
    if (strcmp(tok, "ctrl") == 0 || strcmp(tok, "control") == 0) {
        strncpy(out, "Ctrl", out_size - 1); out[out_size-1] = '\0'; return 1;
    }
    if (strcmp(tok, "shift") == 0) {
        strncpy(out, "Shift", out_size - 1); out[out_size-1] = '\0'; return 1;
    }
    if (strcmp(tok, "alt") == 0
        || strcmp(tok, "option") == 0 || strcmp(tok, "opt") == 0) {
        strncpy(out, "Alt", out_size - 1); out[out_size-1] = '\0'; return 1;
    }
    if (strcmp(tok, "win") == 0   || strcmp(tok, "super") == 0
        || strcmp(tok, "meta") == 0
        || strcmp(tok, "cmd") == 0 || strcmp(tok, "command") == 0) {
        strncpy(out, "Win", out_size - 1); out[out_size-1] = '\0'; return 1;
    }
    return 0;
}

/* Parse argv[1] into g_spec. Returns 1 on success, 0 on parse failure. */
static int parse_hotkey_spec(const char* raw, HotkeySpec* spec) {
    memset(spec, 0, sizeof(*spec));
    strncpy(spec->spec, raw, sizeof(spec->spec) - 1);
    spec->spec[sizeof(spec->spec) - 1] = '\0';

    char buf[256];
    strncpy(buf, raw, sizeof(buf) - 1);
    buf[sizeof(buf) - 1] = '\0';

    /* Split on '+'. strtok() is not thread-safe but we're single-threaded at
     * startup, so this is fine and portable across MSVC + MinGW. */
    char* tok = strtok(buf, "+");
    while (tok != NULL) {
        char t[64];
        strncpy(t, tok, sizeof(t) - 1);
        t[sizeof(t) - 1] = '\0';
        trim(t);
        strip_brackets(t);
        trim(t);
        to_lower(t);
        if (t[0] == '\0') return 0;

        /* Reject "fn" on Windows — the Fn key is firmware-only and never
         * surfaces as a Win32 keystroke. */
        if (strcmp(t, "fn") == 0) return 0;

        char mod[16];
        char key[32];

        if (normalize_modifier(t, mod, sizeof(mod))) {
            /* deduplicate */
            int dup = 0;
            int i;
            for (i = 0; i < spec->modifier_count; i++) {
                if (strcmp(spec->modifiers[i], mod) == 0) { dup = 1; break; }
            }
            if (!dup) {
                if (spec->modifier_count
                    >= (int)(sizeof(spec->modifiers) / sizeof(spec->modifiers[0]))) {
                    return 0;
                }
                strncpy(spec->modifiers[spec->modifier_count], mod,
                        sizeof(spec->modifiers[0]) - 1);
                spec->modifiers[spec->modifier_count]
                    [sizeof(spec->modifiers[0]) - 1] = '\0';
                spec->modifier_count++;
            }
        } else if (normalize_key_name(t, key, sizeof(key))) {
            /* at most one main key */
            if (spec->main_key[0] != '\0') return 0;
            strncpy(spec->main_key, key, sizeof(spec->main_key) - 1);
            spec->main_key[sizeof(spec->main_key) - 1] = '\0';
        } else {
            return 0;
        }

        tok = strtok(NULL, "+");
    }

    /* Must have at least one token (modifier or main key). */
    if (spec->modifier_count == 0 && spec->main_key[0] == '\0') {
        return 0;
    }

    spec->is_modifier_only  = (spec->main_key[0] == '\0');
    spec->is_caps_lock_only =
        (spec->main_key[0] != '\0'
         && strcmp(spec->main_key, "CapsLock") == 0
         && spec->modifier_count == 0);

    return 1;
}

/* ===========================================================================
 * Suppression logic
 *
 * Decide whether a given keyDown event should be suppressed (swallowed) so it
 * never reaches the foreground app. The rules (per spec):
 *
 *   (1) "<caps_lock>" alone          → swallow VK_CAPITAL keyDown so the OS
 *                                      doesn't toggle caps state
 *   (2) modifier-only hotkey         → swallow the keyDown of any configured
 *                                      modifier (both left & right sides)
 *   (3) combo (mods + main key)      → swallow the main key only when ALL
 *                                      configured modifiers are currently held
 *   (4) single main key alone        → don't swallow per spec
 *                                      ("otherwise: don't suppress")
 *
 * For combos we check currently-held modifiers via GetAsyncKeyState(). This
 * is the same API the old polling backend used; we use it here only to read
 * instantaneous modifier state from within the hook callback (not to poll).
 * =========================================================================== */

static int modifier_held(const char* m) {
    if (strcmp(m, "Ctrl") == 0) {
        return (GetAsyncKeyState(VK_LCONTROL) & 0x8000) != 0
            || (GetAsyncKeyState(VK_RCONTROL) & 0x8000) != 0;
    }
    if (strcmp(m, "Shift") == 0) {
        return (GetAsyncKeyState(VK_LSHIFT) & 0x8000) != 0
            || (GetAsyncKeyState(VK_RSHIFT) & 0x8000) != 0;
    }
    if (strcmp(m, "Alt") == 0) {
        return (GetAsyncKeyState(VK_LMENU) & 0x8000) != 0
            || (GetAsyncKeyState(VK_RMENU) & 0x8000) != 0;
    }
    if (strcmp(m, "Win") == 0) {
        return (GetAsyncKeyState(VK_LWIN) & 0x8000) != 0
            || (GetAsyncKeyState(VK_RWIN) & 0x8000) != 0;
    }
    return 0;
}

/* Returns 1 if the keyDown for this VK should be swallowed. */
static int should_suppress_keydown(int vk, const HotkeySpec* spec) {
    /* (1) CapsLock alone — swallow so the OS doesn't toggle caps state. */
    if (spec->is_caps_lock_only && vk == VK_CAPITAL) {
        return 1;
    }

    /* (2) Modifier-only hotkey — swallow the keyDown of any configured
     *     modifier (both L and R sides, since mod_name_for_vk() collapses
     *     them). */
    if (spec->is_modifier_only) {
        const char* mod_name = mod_name_for_vk(vk);
        if (mod_name != NULL) {
            int i;
            for (i = 0; i < spec->modifier_count; i++) {
                if (strcmp(spec->modifiers[i], mod_name) == 0) return 1;
            }
        }
        return 0;
    }

    /* (3) Combo (modifiers + main key) — swallow the main key only when ALL
     *     configured modifiers are currently held. A single main-key-alone
     *     hotkey (modifiers empty) falls into "otherwise" and is NOT
     *     suppressed. */
    if (spec->main_key[0] != '\0' && spec->modifier_count > 0) {
        const char* name = name_for_vk(vk);
        if (name == NULL || strcmp(name, spec->main_key) != 0) {
            return 0;
        }
        int i;
        for (i = 0; i < spec->modifier_count; i++) {
            if (!modifier_held(spec->modifiers[i])) return 0;
        }
        return 1;
    }

    /* (4) Otherwise — don't swallow. */
    return 0;
}

/* ===========================================================================
 * Low-level keyboard hook callback
 *
 * SetWindowsHookEx(WH_KEYBOARD_LL) installs a *global* low-level keyboard
 * hook. The callback runs in the context of the thread that called
 * SetWindowsHookEx — i.e. our main thread — and is dispatched synchronously
 * by the OS during that thread's message pump. Therefore all stdout writes
 * from here are single-threaded; no locking is required.
 *
 * Returning a non-zero value from the callback (without calling
 * CallNextHookEx) swallows the event so it never reaches the foreground app
 * or any other hook in the chain.
 * =========================================================================== */

static LRESULT CALLBACK LowLevelKeyboardProc(int nCode,
                                             WPARAM wParam,
                                             LPARAM lParam) {
    if (nCode == HC_ACTION) {
        KBDLLHOOKSTRUCT* kb = (KBDLLHOOKSTRUCT*)lParam;
        int    vk    = (int)kb->vkCode;
        DWORD  flags = kb->flags;

        if (wParam == WM_KEYDOWN || wParam == WM_SYSKEYDOWN) {
            /* Modifiers and non-modifiers take different wire paths. */
            const char* mod_name = mod_name_for_vk(vk);
            if (mod_name != NULL) {
                char buf[64];
                snprintf(buf, sizeof(buf), "MOD_DOWN:%s", mod_name);
                emit(buf);
            } else {
                const char* name = name_for_event(vk, flags);
                if (name != NULL) {
                    char buf[64];
                    snprintf(buf, sizeof(buf), "KEY_DOWN:%s", name);
                    emit(buf);
                }
            }

            /* Suppression: if this keystroke matches the registered hotkey's
             * suppression rule, swallow it. We still emitted the wire event
             * above so Python can react; we just stop the foreground app from
             * seeing the keystroke. */
            if (should_suppress_keydown(vk, &g_spec)) {
                g_suppressed_vk = vk;
                return 1;   /* swallow — do not call CallNextHookEx */
            }
        } else if (wParam == WM_KEYUP || wParam == WM_SYSKEYUP) {
            /* If we swallowed the matching keyDown, swallow its keyUp too so
             * the foreground app doesn't see an orphan key-up. */
            if (g_suppressed_vk == vk) {
                g_suppressed_vk = 0;
                return 1;   /* swallow */
            }

            const char* mod_name = mod_name_for_vk(vk);
            if (mod_name != NULL) {
                char buf[64];
                snprintf(buf, sizeof(buf), "MOD_UP:%s", mod_name);
                emit(buf);
            } else {
                const char* name = name_for_event(vk, flags);
                if (name != NULL) {
                    char buf[64];
                    snprintf(buf, sizeof(buf), "KEY_UP:%s", name);
                    emit(buf);
                }
            }
        }
    }

    /* Pass the event down the hook chain so other hooks (and ultimately the
     * foreground app) still see it — unless we returned early above. */
    return CallNextHookEx(NULL, nCode, wParam, lParam);
}

/* ===========================================================================
 * Console control handler (Ctrl-C / Ctrl-Break / shutdown)
 *
 * Windows has no real SIGTERM; the parent Python process either calls
 * TerminateProcess() (unconditional — no chance to clean up) or sends a
 * console control event via GenerateConsoleCtrlEvent() (which is what we
 * handle here). We catch all the close/shutdown/break events, unhook the
 * keyboard hook, and exit cleanly.
 * =========================================================================== */

static BOOL WINAPI console_handler(DWORD ctrl) {
    switch (ctrl) {
        case CTRL_C_EVENT:
        case CTRL_BREAK_EVENT:
        case CTRL_CLOSE_EVENT:
        case CTRL_LOGOFF_EVENT:
        case CTRL_SHUTDOWN_EVENT:
            /* signal the stdin reader thread to exit. */
            InterlockedExchange(&g_should_exit, 1);
            if (g_hook != NULL) {
                UnhookWindowsHookEx(g_hook);
                g_hook = NULL;
            }
            /* exit() runs atexit handlers, but we have none — straight exit. */
            exit(0);
            return TRUE;
        default:
            return FALSE;
    }
}

/* ===========================================================================
 * Main entry
 * =========================================================================== */

int main(int argc, char** argv) {
    g_main_thread_id = GetCurrentThreadId();

    /* (0) stdout must NOT be fully buffered when piped. On Windows, MSVC's
     *     CRT treats _IOLBF (line buffering) the same as _IOFBF (full
     *     buffering) for non-terminal streams, so we use _IONBF (no
     *     buffering) — every fputs/fputc writes straight to the file handle.
     *     emit() also calls fflush() after each line as belt-and-suspenders. */
    setvbuf(stdout, NULL, _IONBF, 0);
    g_out = stdout;

    /* initialize the emit critical section BEFORE any thread that
     * calls emit() is started. The hook callback (main thread) and the
     * stdin reader thread both call emit(). */
    InitializeCriticalSection(&g_emit_lock);
    g_emit_lock_inited = 1;

    /* (1) Parse argv[1] — the hotkey spec. We don't match against it (Python
     *     does), but we validate it so we can fail fast with ERROR on bad
     *     input, and we use it to drive suppression decisions in the hook. */
    if (argc < 2) {
        emit("ERROR:Missing hotkey spec argument");
        return 1;
    }
    if (!parse_hotkey_spec(argv[1], &g_spec)) {
        char buf[300];
        snprintf(buf, sizeof(buf), "ERROR:Invalid hotkey spec: %s", argv[1]);
        emit(buf);
        return 1;
    }

    /* parse optional ``--log-file <path>`` argument. Also accept
     *     a bare positional argv[2] as the log path for forward compatibility. */
    const char* log_file_path = NULL;
    for (int i = 2; i < argc; i++) {
        if (strcmp(argv[i], "--log-file") == 0 && i + 1 < argc) {
            log_file_path = argv[++i];
        } else if (argv[i][0] != '-' && log_file_path == NULL) {
            log_file_path = argv[i];
        }
    }
    if (log_file_path != NULL && log_file_path[0] != '\0') {
        g_diag_log = fopen(log_file_path, "a");
        if (g_diag_log == NULL) {
            log_diag("WARN: failed to open --log-file (errno=%d)", errno);
        }
    }
    log_diag("windows-key-listener starting; spec=%s; log_file=%s",
             argv[1], log_file_path ? log_file_path : "<none>");

    /* start the stdin reader thread so we can respond to PING with
     *     PONG. _beginthreadex returns 0 on failure. The thread is detached. */
    uintptr_t stdin_tid = _beginthreadex(NULL, 0, stdin_reader_thread, NULL, 0, NULL);
    if (stdin_tid == 0) {
        log_diag("WARN: failed to start stdin reader thread; PING/PONG disabled");
    } else {
        log_diag("stdin reader thread started (PING/PONG enabled)");
        CloseHandle((HANDLE)stdin_tid);
    }

    /* (2) Install the low-level keyboard hook. */
    g_hook = SetWindowsHookEx(WH_KEYBOARD_LL, LowLevelKeyboardProc, NULL, 0);
    if (g_hook == NULL) {
        DWORD err = GetLastError();
        char buf[128];
        snprintf(buf, sizeof(buf),
                 "ERROR:Failed to install keyboard hook (error=%lu)", err);
        emit(buf);
        log_diag("ERROR: SetWindowsHookEx failed (error=%lu)", err);
        return 1;
    }
    log_diag("keyboard hook installed");

    /* (3) Install the console control handler for clean shutdown on Ctrl-C,
     *     window-close, logoff, and shutdown. */
    SetConsoleCtrlHandler(console_handler, TRUE);

    /* (4) Announce readiness. Python waits for this line before considering
     *     the backend "up". */
    emit("READY");
    /* immediately announce our wire-protocol version. */
    {
        char vbuf[32];
        snprintf(vbuf, sizeof(vbuf), "VERSION:%s", NATIVE_BINARY_VERSION);
        emit(vbuf);
    }
    log_diag("READY emitted; version=%s", NATIVE_BINARY_VERSION);

    /* (5) Message pump — runs until WM_QUIT is received or until the console
     *     control handler calls exit(0) directly. */
    MSG msg;
    while (GetMessage(&msg, NULL, 0, 0) > 0) {
        TranslateMessage(&msg);
        DispatchMessage(&msg);
    }

    /* (6) Cleanup — normally we exit() from the console handler before
     *     reaching here, but if the message pump returns (WM_QUIT without an
     *     exit()) we still unhook cleanly. */
    InterlockedExchange(&g_should_exit, 1);
    if (g_hook != NULL) {
        UnhookWindowsHookEx(g_hook);
        g_hook = NULL;
    }
    if (g_diag_log != NULL) {
        fclose(g_diag_log);
        g_diag_log = NULL;
    }
    return 0;
}
