# ruff: noqa: A001, A002, N802, N803, N816
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

# TASK-14: ``kAudioHardwarePropertyDevices`` is the system-level
# selector used by ``microphone_watcher_coreaudio`` to subscribe to
# device plug/unplug events.  Previously missing from this stub, which
# made ``from CoreAudio import kAudioHardwarePropertyDevices`` raise
# ``missing-module-attribute`` on the Linux CI runner.
kAudioHardwarePropertyDevices: Any

# TASK-11: ``kAudioHardwarePropertyDefaultOutputDevice`` is the system-
# level selector used by ``MacVolumeBackend._get_default_output_device``
# to fetch the AudioDeviceID of the current default output device.
# Queried on ``kAudioObjectSystemObject`` with scope
# ``kAudioObjectPropertyScopeGlobal``.  Returns a UInt32 (AudioDeviceID).
kAudioHardwarePropertyDefaultOutputDevice: Any

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
# TASK-14: listener registration functions used by
# ``microphone_watcher_coreaudio.py`` to subscribe to CoreAudio
# property-change events.  Declared with permissive ``Any`` parameter
# types to match the rest of this stub.
def AudioObjectAddPropertyListener(
    inObjectID: Any,
    inAddress: Any,
    inListener: Any,
    inClientData: Any,
) -> int: ...
def AudioObjectRemovePropertyListener(
    inObjectID: Any,
    inAddress: Any,
    inListener: Any,
    inClientData: Any,
) -> int: ...

__all__: list[str]
