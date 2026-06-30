// =============================================================================
// Voice Typer — macOS native key listener
//
// Emits line-delimited key events on stdout for the Python parent process to
// match against the registered hotkey. Modeled on Freestyle's
// macos-key-listener.swift (trimmed). Uses THREE event sources:
//
//   (a) NSEvent global monitor for .flagsChanged — FN + modifier transitions
//   (b) NSEvent global monitor for .keyDown — non-modifier key-down events
//   (c) CGEventTap on .keyDown/.keyUp — for reliable key-up delivery (NSEvent
//       global monitors miss keyUp) AND for suppressing the matched hotkey's
//       keystroke so it doesn't reach the foreground app
//
// Wire protocol (one event per line, newline-terminated):
//   READY                          # emitted once after init succeeds
//   FN_DOWN / FN_UP                # macOS only — Fn/Globe edge-detected
//   KEY_DOWN:<Name>                # non-modifier key pressed
//   KEY_UP:<Name>                  # non-modifier key released
//   MOD_DOWN:<Name>                # modifier pressed (Ctrl, Shift, Alt, Cmd)
//   MOD_UP:<Name>                  # modifier released
//   ERROR:<message>                # fatal error, then exit(1)
//
// Build:
//   swiftc -O macos-key-listener.swift -framework Cocoa -framework CoreGraphics \
//          -o macos-key-listener
//
// SPDX-License-Identifier: project-wide
// =============================================================================

import Cocoa
import CoreGraphics

// MARK: - Wire protocol emitter

/// Serial queue so stdout writes from multiple event-callback threads are
/// atomic and never interleave. All event sources fire on the main thread in
/// our setup, but the queue is cheap insurance.
private let emitQueue = DispatchQueue(label: "voice-typer.key-listener.emit")

/// Emit a single line (without trailing newline) atomically to stdout.
/// Safe to call from any thread.
func emit(_ line: String) {
    let payload = (line + "\n").data(using: .utf8) ?? Data()
    emitQueue.sync {
        FileHandle.standardOutput.write(payload)
    }
}

// MARK: - Hotkey spec parsing

/// Parsed representation of the hotkey spec supplied via argv[1]. The binary
/// does NOT do hotkey matching — Python does. We only parse so we can (a)
/// reject invalid specs early with ERROR, and (b) know which keystrokes to
/// suppress in the CGEventTap so they don't reach the foreground app.
struct HotkeySpec {
    /// Normalized modifier names present in the spec
    /// (subset of "Ctrl", "Shift", "Alt", "Cmd", "Fn").
    let modifiers: Set<String>
    /// Normalized main-key wire name (e.g. "V", "F2", "Space", "CapsLock"),
    /// or nil for modifier-only hotkeys.
    let mainKey: String?
    /// True if the spec is exactly `<fn>` alone.
    let isFnOnly: Bool
    /// True if the spec is exactly `<caps_lock>` alone.
    let isCapsLockOnly: Bool
    /// True if the spec has no main key (e.g. `<alt>`, `<ctrl>+<shift>`, `<fn>`).
    let isModifierOnly: Bool
}

/// Parse a pynput-style hotkey spec such as `<ctrl>+<alt>+v`, `<fn>`, `<f2>`.
/// Returns nil if the spec is empty or contains an unknown token.
func parseHotkeySpec(_ raw: String) -> HotkeySpec? {
    var tokens: [String] = []
    for part in raw.split(separator: "+") {
        var t = String(part).trimmingCharacters(in: .whitespaces)
        // Strip < > brackets if present
        if t.hasPrefix("<") && t.hasSuffix(">") {
            t = String(t.dropFirst().dropLast())
        }
        t = t.lowercased()
        if t.isEmpty { return nil }
        tokens.append(t)
    }
    if tokens.isEmpty { return nil }

    var modifiers: Set<String> = []
    var mainKey: String? = nil

    for t in tokens {
        switch t {
        case "fn":
            modifiers.insert("Fn")
        case "ctrl", "control":
            modifiers.insert("Ctrl")
        case "shift":
            modifiers.insert("Shift")
        case "alt", "option", "opt":
            modifiers.insert("Alt")
        case "cmd", "command", "win", "super", "meta":
            modifiers.insert("Cmd")
        default:
            // Must be a main key (letter, digit, function key, special key).
            guard let norm = normalizeKeyName(t) else { return nil }
            if mainKey != nil { return nil } // more than one main key — invalid
            mainKey = norm
        }
    }

    let isModifierOnly = (mainKey == nil)
    let isFnOnly = (modifiers == ["Fn"] && mainKey == nil)
    let isCapsLockOnly = (mainKey == "CapsLock" && modifiers.isEmpty)

    return HotkeySpec(
        modifiers: modifiers,
        mainKey: mainKey,
        isFnOnly: isFnOnly,
        isCapsLockOnly: isCapsLockOnly,
        isModifierOnly: isModifierOnly
    )
}

/// Normalize a pynput-style token to our wire-protocol key name.
/// - Single ASCII letter → uppercase ("a" → "A")
/// - Single ASCII digit → preserved ("5" → "5")
/// - Function / special keys → mapped to canonical wire names
/// Returns nil for unrecognized tokens.
func normalizeKeyName(_ t: String) -> String? {
    // Single ASCII letter → uppercase
    if t.count == 1, let c = t.first, c.isLetter, c.isASCII {
        return c.uppercased()
    }
    // Single ASCII digit → preserved
    if t.count == 1, let c = t.first, c.isNumber, c.isASCII {
        return String(c)
    }
    // Function + special keys
    let map: [String: String] = [
        "f1": "F1", "f2": "F2", "f3": "F3", "f4": "F4",
        "f5": "F5", "f6": "F6", "f7": "F7", "f8": "F8",
        "f9": "F9", "f10": "F10", "f11": "F11", "f12": "F12",
        "space": "Space", "enter": "Enter", "return": "Enter", "tab": "Tab",
        "esc": "Esc", "escape": "Esc", "backspace": "Backspace",
        "insert": "Insert", "delete": "Delete", "home": "Home", "end": "End",
        "page_up": "PageUp", "pageup": "PageUp",
        "page_down": "PageDown", "pagedown": "PageDown",
        "caps_lock": "CapsLock", "capslock": "CapsLock",
        "num_lock": "NumLock", "numlock": "NumLock",
        "scroll_lock": "ScrollLock", "scrolllock": "ScrollLock",
        "print_screen": "PrintScreen", "printscreen": "PrintScreen",
        "pause": "Pause",
        "up": "Up", "down": "Down", "left": "Left", "right": "Right",
    ]
    return map[t]
}

// MARK: - Key code → wire name

/// macOS virtual keyCodes for non-printable (special) keys. Letters and digits
/// are resolved at event time via `charactersIgnoringModifiers` so we respect
/// the user's keyboard layout. Source: Carbon/HIToolbox/Events.h.
private let specialKeyCodes: [UInt16: String] = [
    // Function keys (explicit per spec)
    122: "F1", 120: "F2", 99: "F3", 118: "F4",
    96:  "F5", 97:  "F6", 98:  "F7", 100: "F8",
    101: "F9", 109: "F10", 103: "F11", 111: "F12",
    // Navigation / editing
    36:  "Enter",    // kVK_Return
    48:  "Tab",      // kVK_Tab
    49:  "Space",    // kVK_Space
    53:  "Esc",      // kVK_Escape
    51:  "Backspace",// kVK_Delete (backspace)
    117: "Delete",   // kVK_ForwardDelete
    114: "Insert",   // kVK_Help (Insert on PC keyboards)
    115: "Home",     // kVK_Home
    119: "End",      // kVK_End
    116: "PageUp",   // kVK_PageUp
    121: "PageDown", // kVK_PageDown
    // Arrows
    123: "Left", 124: "Right", 125: "Down", 126: "Up",
]

/// Modifier keyCodes — these produce `.flagsChanged` events on macOS, not
/// `.keyDown`. We skip them in the keyDown handler so we don't double-emit.
/// (Caps Lock 57 and Fn 63 are also here; both are handled via flagsChanged.)
private let modifierKeyCodes: Set<UInt16> = [
    55, 54,   // left / right Cmd
    56, 60,   // left / right Shift
    58, 61,   // left / right Alt (Option)
    59, 62,   // left / right Ctrl
    57,       // Caps Lock
    63,       // Fn
]

/// Map an NSEvent to its wire-protocol name. Returns nil for modifier keys
/// (handled separately) or unrecognized printable keys.
func nameForNSEvent(_ event: NSEvent) -> String? {
    if let name = specialKeyCodes[event.keyCode] { return name }
    if modifierKeyCodes.contains(event.keyCode) { return nil }
    // Printable letter/digit — respect keyboard layout via charactersIgnoringModifiers
    if let chars = event.charactersIgnoringModifiers,
       let first = chars.uppercased().first,
       first.isASCII, (first.isLetter || first.isNumber) {
        return String(first)
    }
    return nil
}

/// Same as `nameForNSEvent` but for a CGEvent (used in the CGEventTap callback).
/// We wrap the CGEvent in an NSEvent to access `charactersIgnoringModifiers`.
func nameForCGEvent(_ cgEvent: CGEvent) -> String? {
    let keyCode = UInt16(cgEvent.getIntegerValueField(.keyboardEventKeycode))
    if let name = specialKeyCodes[keyCode] { return name }
    if modifierKeyCodes.contains(keyCode) { return nil }
    if let nsEvent = NSEvent(cgEvent: cgEvent),
       let chars = nsEvent.charactersIgnoringModifiers,
       let first = chars.uppercased().first,
       first.isASCII, (first.isLetter || first.isNumber) {
        return String(first)
    }
    return nil
}

// MARK: - Shared state (mutated from main-thread callbacks only)

/// Mutable state shared between the three event sources. All callbacks in our
/// setup fire on the main thread (NSEvent global monitors are main-thread;
/// CGEventTap source is added to the main run loop), so no locking is needed
/// for the fields below. The `emit()` helper still uses a serial queue so
/// stdout writes are atomic.
final class TapContext {
    var machPort: CFMachPort?
    let hotkey: HotkeySpec

    // Previous modifier flag state — used for edge detection so we only emit
    // on false→true / true→false transitions, not on every flagsChanged event.
    var ctrl = false
    var shift = false
    var alt = false
    var cmd = false
    var fn = false

    // Suppression: when we swallow a keyDown, remember its keyCode so the
    // matching keyUp is also swallowed — otherwise the foreground app sees an
    // orphan keyUp (keydown suppressed, keyup delivered), which can confuse it.
    var suppressedKeyCode: UInt16? = nil

    init(hotkey: HotkeySpec) {
        self.hotkey = hotkey
    }
}

// MARK: - Modifier / FN event handling (NSEvent .flagsChanged monitor)

/// Handle a `.flagsChanged` event: edge-detect each modifier and emit the
/// appropriate MOD_DOWN/MOD_UP, FN_DOWN/FN_UP, or KEY_DOWN:CapsLock line.
///
/// Critical: detect FN via `event.modifierFlags.contains(.function)` (bit 23)
/// — NOT `keyCode == 63`. The `.function` flag is the semantic "Fn is held"
/// bit. Edge-detecting it via `var fn = false` prevents spurious FN_DOWN /
/// FN_UP fires when unrelated modifiers (Caps Lock, Shift) change state.
func handleFlagsChanged(_ event: NSEvent, context: TapContext) {
    let flags = event.modifierFlags

    let newCtrl  = flags.contains(.control)
    let newShift = flags.contains(.shift)
    let newAlt   = flags.contains(.option)
    let newCmd   = flags.contains(.command)
    let newFn    = flags.contains(.function)   // bit 23 — the "Fn held" flag

    if newCtrl  != context.ctrl  { emit(newCtrl  ? "MOD_DOWN:Ctrl"  : "MOD_UP:Ctrl")  ; context.ctrl  = newCtrl  }
    if newShift != context.shift { emit(newShift ? "MOD_DOWN:Shift" : "MOD_UP:Shift") ; context.shift = newShift }
    if newAlt   != context.alt   { emit(newAlt   ? "MOD_DOWN:Alt"   : "MOD_UP:Alt")   ; context.alt   = newAlt   }
    if newCmd   != context.cmd   { emit(newCmd   ? "MOD_DOWN:Cmd"   : "MOD_UP:Cmd")   ; context.cmd   = newCmd   }

    // Caps Lock arrives as a flagsChanged event with keyCode 57. There is no
    // separate key-up on macOS (it's a latching toggle). Emit KEY_DOWN on
    // every press; Python treats it as a one-shot trigger.
    if event.keyCode == 57 {
        emit("KEY_DOWN:CapsLock")
    }

    // FN: edge-detect via the .function flag. The CGEventTap suppression logic
    // relies on these transitions being edge-detected — otherwise pressing
    // Shift while Fn is held would spuriously re-fire FN_DOWN.
    if newFn != context.fn {
        emit(newFn ? "FN_DOWN" : "FN_UP")
        context.fn = newFn
    }
}

// MARK: - Key-down handling (NSEvent .keyDown monitor)

/// Handle a `.keyDown` event for a non-modifier key. Modifier keyCodes are
/// skipped (handled by `handleFlagsChanged`).
func handleKeyDown(_ event: NSEvent, context: TapContext) {
    if modifierKeyCodes.contains(event.keyCode) { return }
    guard let name = nameForNSEvent(event) else { return }
    emit("KEY_DOWN:\(name)")
}

// MARK: - Hotkey suppression (CGEventTap)

/// Map a modifier keyCode → its wire name (used to decide whether to suppress
/// when the hotkey is modifier-only). Both left and right sides map to the
/// same wire name.
private let keyCodeToModifier: [UInt16: String] = [
    55: "Cmd", 54: "Cmd",
    56: "Shift", 60: "Shift",
    58: "Alt", 61: "Alt",
    59: "Ctrl", 62: "Ctrl",
]

/// Decide whether the given key-down event should be suppressed (swallowed so
/// it never reaches the foreground app). Suppression rules (per spec):
///   - `<fn>` alone               → never swallow (Fn never produces a keystroke)
///   - `<caps_lock>` alone         → swallow keyCode 57 (so Caps state doesn't toggle)
///   - `<alt>`/`<ctrl>`/`<shift>`/`<cmd>` alone → swallow that modifier's keyDown
///   - combo (modifiers + main key) → swallow the main key when all modifiers held
///   - otherwise (e.g. single main key like `<f2>`) → don't swallow
func shouldSuppressKeyDown(event: CGEvent, context: TapContext) -> Bool {
    let keyCode = UInt16(event.getIntegerValueField(.keyboardEventKeycode))

    // (1) Fn alone — never produces a keystroke; nothing to swallow.
    if context.hotkey.isFnOnly { return false }

    // (2) Caps Lock alone — swallow so the OS doesn't toggle caps state.
    if context.hotkey.isCapsLockOnly && keyCode == 57 { return true }

    // (3) Modifier-only hotkey (e.g. <alt>, <ctrl>+<shift>) — swallow the
    //     keyDown of any of the configured modifiers (both left & right sides).
    if context.hotkey.isModifierOnly {
        if let modName = keyCodeToModifier[keyCode],
           context.hotkey.modifiers.contains(modName) {
            return true
        }
        return false
    }

    // (4) Combo (modifiers + main key) — swallow the main key only when ALL
    //     configured modifiers are currently held. A single main-key-alone
    //     hotkey (e.g. <f2>, modifiers empty) falls into "otherwise" and is
    //     NOT suppressed per spec — let it pass through.
    if let mainKey = context.hotkey.mainKey, !context.hotkey.modifiers.isEmpty {
        guard let name = nameForCGEvent(event), name == mainKey else { return false }
        let flags = event.flags
        if context.hotkey.modifiers.contains("Ctrl")  && !flags.contains(.maskControl)    { return false }
        if context.hotkey.modifiers.contains("Shift") && !flags.contains(.maskShift)      { return false }
        if context.hotkey.modifiers.contains("Alt")   && !flags.contains(.maskAlternate)  { return false }
        if context.hotkey.modifiers.contains("Cmd")   && !flags.contains(.maskCommand)    { return false }
        if context.hotkey.modifiers.contains("Fn")    && !flags.contains(.maskSecondaryFn){ return false }
        return true
    }

    // (5) Otherwise — don't swallow.
    return false
}

// MARK: - CGEventTap callback

/// The CGEventTap callback. Must be `@convention(c)` — it cannot capture any
/// state, so all context flows through the `userInfo` pointer we pass to
/// `CGEvent.tapCreate`. Top-level Swift functions (`emit`, `nameForCGEvent`,
/// `shouldSuppressKeyDown`) are callable from a @convention(c) closure
/// because they don't require captured state.
let eventTapCallback: CGEventTapCallBack = { _, type, cgEvent, userInfo in
    guard let userInfo = userInfo else {
        return Unmanaged.passUnretained(cgEvent)
    }
    let ctx = Unmanaged<TapContext>.fromOpaque(userInfo).takeUnretainedValue()

    // The tap can be disabled by the OS on timeout or by user-input recursion.
    // Re-enable it so we keep receiving events.
    if type == .tapDisabledByTimeout || type == .tapDisabledByUserInput {
        if let port = ctx.machPort {
            CGEvent.tapEnable(tap: port, enable: true)
        }
        return Unmanaged.passUnretained(cgEvent)
    }

    let keyCode = UInt16(cgEvent.getIntegerValueField(.keyboardEventKeycode))

    switch type {
    case .keyDown:
        // DO NOT emit here — the NSEvent .keyDown monitor handles emission to
        // avoid duplicates. We only check whether to suppress the matched
        // hotkey's keystroke so it doesn't reach the foreground app.
        if shouldSuppressKeyDown(event: cgEvent, context: ctx) {
            ctx.suppressedKeyCode = keyCode
            return nil  // swallow — event won't be delivered downstream
        }

    case .keyUp:
        // If we swallowed the matching keyDown, swallow its keyUp too so the
        // foreground app doesn't see an orphan key-up.
        if ctx.suppressedKeyCode == keyCode {
            ctx.suppressedKeyCode = nil
            return nil
        }
        // Otherwise emit KEY_UP:<Name>. NSEvent global monitors MISS keyUp,
        // so the CGEventTap is the only reliable source for key-up delivery.
        if let name = nameForCGEvent(cgEvent) {
            emit("KEY_UP:\(name)")
        }

    default:
        break
    }

    return Unmanaged.passUnretained(cgEvent)
}

// MARK: - Main entry

// (0) Parse argv[1] — the hotkey spec. We don't match against it (Python
// does), but we validate it so we can fail fast with ERROR on bad input.
guard CommandLine.arguments.count >= 2 else {
    emit("ERROR:Missing hotkey spec argument")
    exit(1)
}
let specArg = CommandLine.arguments[1]
guard let parsedHotkey = parseHotkeySpec(specArg) else {
    emit("ERROR:Invalid hotkey spec: \(specArg)")
    exit(1)
}

// (1) Set up the application. .accessory policy = no Dock icon, no menu bar
// takeover — we run as a faceless background helper.
let app = NSApplication.shared
app.setActivationPolicy(.accessory)

// Shared state for all three event sources.
let context = TapContext(hotkey: parsedHotkey)

// (2a) NSEvent global monitor for .flagsChanged — FN + modifier transitions.
let flagsMonitor = NSEvent.addGlobalMonitorForEvents(matching: .flagsChanged) { event in
    handleFlagsChanged(event, context: context)
}
guard flagsMonitor != nil else {
    emit("ERROR:Failed to create NSEvent monitor")
    exit(1)
}

// (2b) NSEvent global monitor for .keyDown — non-modifier key-down events.
let keyDownMonitor = NSEvent.addGlobalMonitorForEvents(matching: .keyDown) { event in
    handleKeyDown(event, context: context)
}
guard keyDownMonitor != nil else {
    emit("ERROR:Failed to create NSEvent monitor")
    exit(1)
}

// (2c) CGEventTap — reliable key-up delivery + matched-hotkey suppression.
//     `.defaultTap` (vs `.listenOnly`) is what gives us suppression power,
//     but it requires Accessibility permission. `tapCreate` returns nil if
//     the permission isn't granted — detect and emit ERROR.
// Bitmask of CGEventType values the tap is interested in. We need both
// keyDown (for suppression) and keyUp (for reliable key-up delivery).
let eventsOfInterest: CGEventMask =
    (CGEventMask(1) << CGEventType.keyDown.rawValue)
    | (CGEventMask(1) << CGEventType.keyUp.rawValue)

let userInfo = Unmanaged.passUnretained(context).toOpaque()
guard let machPort = CGEvent.tapCreate(
    tap: .cgSessionEventTap,
    place: .headInsertEventTap,
    options: .defaultTap,
    eventsOfInterest: eventsOfInterest,
    callback: eventTapCallback,
    userInfo: userInfo
) else {
    emit("ERROR:Accessibility permission required. Grant it in System Settings → Privacy & Security → Accessibility.")
    exit(1)
}
context.machPort = machPort

// Schedule the tap on the current (main) run loop and enable it.
guard let runLoopSource = CFMachPortCreateRunLoopSource(kCFAllocatorDefault, machPort, 0) else {
    emit("ERROR:Failed to create run loop source for event tap")
    exit(1)
}
CFRunLoopAddSource(CFRunLoopGetCurrent(), runLoopSource, .commonModes)
CGEvent.tapEnable(tap: machPort, enable: true)

// (3) SIGTERM handler — the Python parent sends SIGTERM to shut us down.
//     We install SIG_IGN first so the default "terminate" action doesn't fire,
//     then a DispatchSource receives the signal on the main queue and gives us
//     a chance to clean up before exit(0).
signal(SIGTERM, SIG_IGN)
let sigtermSource = DispatchSource.makeSignalSource(signal: SIGTERM, queue: .main)
sigtermSource.setEventHandler {
    if let port = context.machPort {
        CGEvent.tapEnable(tap: port, enable: false)
    }
    if let m = flagsMonitor   { NSEvent.removeMonitor(m) }
    if let m = keyDownMonitor { NSEvent.removeMonitor(m) }
    exit(0)
}
sigtermSource.resume()

// (4) All set — announce readiness and enter the run loop. The run loop
//     services the NSEvent monitors and the CGEventTap source.
emit("READY")
app.run()
