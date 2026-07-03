# PYREFLY-001: stub for the `winreg` module (Windows-only stdlib).
#
# pyrefly ships a `winreg` stub, but on non-Windows runners it appears
# to be minimal — voice-typer accesses (OpenKey, SetValueEx, etc.) are
# reported as `missing-attribute`. This stub declares the full API
# surface used by `voice_typer/server/server_platform.py` and
# `voice_typer/server/task_scheduler.py` so pyrefly can follow the
# Windows autostart code paths without reporting false positives.
#
# All function signatures mirror the CPython `winreg` docs.
from typing import Any

# Root key handles (HKEY_*).
HKEY_CLASSES_ROOT: int
HKEY_CURRENT_USER: int
HKEY_LOCAL_MACHINE: int
HKEY_USERS: int
HKEY_CURRENT_CONFIG: int
HKEY_DYN_DATA: int
HKEY_PERFORMANCE_DATA: int
HKEY_PERFORMANCE_NLSTEXT: int
HKEY_PERFORMANCE_TEXT: int

# Access rights (REG_*).
KEY_ALL_ACCESS: int
KEY_CREATE_LINK: int
KEY_CREATE_SUB_KEY: int
KEY_ENUMERATE_SUB_KEYS: int
KEY_EXECUTE: int
KEY_NOTIFY: int
KEY_QUERY_VALUE: int
KEY_READ: int
KEY_SET_VALUE: int
KEY_WOW64_32KEY: int
KEY_WOW64_64KEY: int
KEY_WRITE: int

# Value types (REG_*).
REG_BINARY: int
REG_DWORD: int
REG_DWORD_BIG_ENDIAN: int
REG_DWORD_LITTLE_ENDIAN: int
REG_EXPAND_SZ: int
REG_FULL_RESOURCE_DESCRIPTOR: int
REG_LINK: int
REG_MULTI_SZ: int
REG_NONE: int
REG_QWORD: int
REG_QWORD_LITTLE_ENDIAN: int
REG_RESOURCE_LIST: int
REG_RESOURCE_REQUIREMENTS_LIST: int
REG_SZ: int

# 64-bit view flags.
KEY_WOW64_RES: int

# Types.
HKEY: Any
PyHKEY: Any

def CloseKey(hkey: Any) -> None: ...
def ConnectRegistry(
    computer_name: Any,
    key: int,
) -> Any: ...
def CreateKey(
    key: Any,
    sub_key: Any,
) -> Any: ...
def CreateKeyEx(
    key: Any,
    sub_key: Any,
    reserved: int = ...,
    access: int = ...,
) -> Any: ...
def DeleteKey(key: Any, sub_key: Any) -> None: ...
def DeleteValue(key: Any, value: Any) -> None: ...
def DisableReflectionKey(key: Any) -> None: ...
def EnableReflectionKey(key: Any) -> None: ...
def EnumKey(key: Any, index: int) -> str: ...
def EnumValue(key: Any, index: int) -> tuple: ...
def ExpandEnvironmentStrings(value: Any) -> str: ...
def FlushKey(key: Any) -> None: ...
def LoadKey(key: Any, sub_key: Any, file_name: Any) -> None: ...
def OpenKey(
    key: Any,
    sub_key: Any,
    reserved: int = ...,
    access: int = ...,
) -> Any: ...
def OpenKeyEx(
    key: Any,
    sub_key: Any,
    reserved: int = ...,
    access: int = ...,
) -> Any: ...
def QueryInfoKey(key: Any) -> tuple: ...
def QueryReflectionKey(key: Any) -> bool: ...
def QueryValue(key: Any, sub_key: Any) -> str: ...
def QueryValueEx(key: Any, value_name: Any) -> tuple: ...
def ReflectionKey(key: Any) -> None: ...
def ReplaceKey(
    key: Any,
    sub_key: Any,
    new_file: Any,
    old_file: Any,
) -> None: ...
def RestoreKey(key: Any, file_name: Any, flags: int = ...) -> None: ...
def SaveKey(key: Any, file_name: Any, sec_attr: Any = ...) -> None: ...
def SetValue(
    key: Any,
    sub_key: Any,
    type: int,
    value: Any,
) -> None: ...
def SetValueEx(
    key: Any,
    value_name: Any,
    reserved: int,
    type: int,
    value: Any,
) -> None: ...

__all__: list[str]
