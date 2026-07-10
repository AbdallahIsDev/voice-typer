"""Fix the mutex name in _ensure_single_instance to be a constant string."""
import sys

with open('voice_typer/server/app.py', 'r', encoding='utf-8') as f:
    content = f.read()

old = (
    '    # PLAT-RUN: Include the installation path hash in the mutex name\n'
    '    # so different installations don\'t conflict (e.g. stable vs dev).\n'
    '    import hashlib\n'
    '    install_hash = hashlib.sha256(sys.executable.encode()).hexdigest()[:8]\n'
    '    mutex_name = f"Local\\\\VoiceTyperSingleInstance_{install_hash}"'
)

new = (
    '    # PLAT-RUN-FIXED: The mutex name is now a fixed string so ALL\n'
    '    # VoiceTyper processes (regardless of Python executable) share the\n'
    '    # same mutex. Previously it included sys.executable hash, which let\n'
    '    # different Python executables (python.exe vs pythonw.exe, dev venv\n'
    '    # vs production install) run as separate instances.\n'
    '    mutex_name = "Local\\\\VoiceTyperSingleInstance"'
)

if old in content:
    content = content.replace(old, new)
    with open('voice_typer/server/app.py', 'w', encoding='utf-8') as f:
        f.write(content)
    print('SUCCESS: Mutex name replaced')
else:
    print('FAILED')
    idx = content.find('PLAT-RUN:')
    if idx >= 0:
        chunk = content[idx:idx+250]
        print('Found PLAT-RUN content:')
        print(repr(chunk))
    sys.exit(1)
