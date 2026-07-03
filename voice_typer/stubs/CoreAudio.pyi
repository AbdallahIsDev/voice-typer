# PYREFLY-001: stub for the `CoreAudio` framework (pyobjc-framework-
# CoreAudio, macOS only). Used by `voice_typer/server/volume_backends.py
# ::MacVolumeBackend`.
#
# Import surface (from grep):
#   from CoreAudio import (
#       AudioObjectGetPropertyData,
#       kAudioHardwareServiceDeviceProperty_VirtualMasterVolume,
#       kAudioHardwareServiceSystemObject,
#       kAudioObjectPropertyElementMaster,
#       kAudioObjectPropertyScopeOutput,
#   )
#   from CoreAudio import (
#       kAudioDevicePropertyDeviceIsRunning,
#       kAudioObjectPropertyElementMaster,
#       kAudioObjectPropertyScopeGlobal,
#   )
#
# All values are `Any` (typically UInt32 selectors / scopes / elements
# packed as ints by pyobjc). The real constants are UInt32; we use `Any`
# so callers can pass them straight to `ctypes.c_uint32(...)` without
# type errors.
from typing import Any

# AudioObject selectors (UInt32).
kAudioDevicePropertyDeviceIsRunning: Any
kAudioDevicePropertyDeviceHasForegroundChannels: Any
kAudioHardwareServiceDeviceProperty_VirtualMasterVolume: Any
kAudioHardwareServiceDeviceProperty_VirtualMasterMute: Any
kAudioHardwareServiceDeviceProperty_VirtualMasterBalance: Any

# AudioObject scopes (UInt32).
kAudioObjectPropertyScopeGlobal: Any
kAudioObjectPropertyScopeOutput: Any
kAudioObjectPropertyScopeInput: Any
kAudioObjectPropertyScopePlayThrough: Any

# AudioObject elements (UInt32).
kAudioObjectPropertyElementMaster: Any
kAudioObjectPropertyElementMain: Any

# AudioObject IDs (UInt32).
kAudioHardwareServiceSystemObject: Any
kAudioObjectUnknown: Any
kAudioObjectSystemObject: Any

# Functions.
def AudioObjectGetPropertyData(
    inObjectID: Any,
    inAddress: Any,
    inQualifierDataSize: int,
    inQualifierData: Any,
    ioDataSize: Any,
    outData: Any,
) -> int: ...
def AudioObjectSetPropertyData(
    inObjectID: Any,
    inAddress: Any,
    inQualifierDataSize: int,
    inQualifierData: Any,
    inDataSize: int,
    inData: Any,
) -> int: ...
def AudioObjectHasProperty(
    inObjectID: Any,
    inAddress: Any,
) -> int: ...
def AudioObjectIsPropertySettable(
    inObjectID: Any,
    inAddress: Any,
    outIsSettable: Any,
) -> int: ...

__all__: list[str]
