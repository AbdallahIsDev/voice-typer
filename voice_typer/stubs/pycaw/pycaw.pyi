# ruff: noqa: A001, A002, N802, N803, N816
# PYREFLY-001: stub for `pycaw.pycaw` — the Windows WASAPI wrapper used
# by `voice_typer/server/volume_backends.py::WinVolumeBackend`.
#
# Import surface (from grep):
#   from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume
#   from pycaw.pycaw import IAudioMeterInformation
#   from pycaw.pycaw import AudioUtilities  (again, in get_other_sessions)
#
# Runtime usage (from grep):
#   AudioUtilities.GetSpeakers() -> device with .EndpointVolume / .Activate()
#   AudioUtilities.GetAllSessions() -> list[AudioSession]
#   IAudioEndpointVolume._iid_           (class attr, IID)
#   IAudioEndpointVolume.GetMasterVolumeLevelScalar() -> float
#   IAudioEndpointVolume.GetMute() -> int
#   IAudioEndpointVolume.SetMasterVolumeLevelScalar(level, ctx)
#   IAudioEndpointVolume.SetMute(muted, ctx)
#   IAudioEndpointVolume.QueryInterface(IAudioMeterInformation) -> meter
#   IAudioMeterInformation.GetPeakValue() -> float
#   AudioSession.Process              -> process with .name() -> str
#   AudioSession.SimpleAudioVolume   -> ISimpleAudioVolume
#   AudioSession._ctl                -> ISimpleAudioVolume (legacy)
#   ISimpleAudioVolume.GetMasterVolume() -> float
#   ISimpleAudioVolume.SetMasterVolume(level, ctx)
#
# All types are `Any` because pycaw returns COM pointers whose exact
# types are platform-specific and never imported on non-Windows runners.
from typing import Any

class AudioUtilities:
    """Stub for `pycaw.pycaw.AudioUtilities` (Windows WASAPI utilities)."""
    @staticmethod
    def GetSpeakers() -> Any: ...
    @staticmethod
    def GetMicrophone() -> Any: ...
    @staticmethod
    def GetAllSessions() -> list[Any]: ...
    @staticmethod
    def GetProcessSession(pid: int) -> Any: ...
    @staticmethod
    def GetAudioSession(pid: int) -> Any: ...

class IAudioEndpointVolume:
    """Stub for the COM `IAudioEndpointVolume` interface."""

    _iid_: Any
    _reg_clsid_: Any
    def GetMasterVolumeLevelScalar(self) -> float: ...
    def SetMasterVolumeLevelScalar(self, level: float, ctx: Any) -> None: ...
    def GetMute(self) -> int: ...
    def SetMute(self, muted: int, ctx: Any) -> None: ...
    def GetVolumeStepInfo(self, step: Any, stepCount: Any) -> None: ...
    def VolumeStepUp(self, ctx: Any) -> None: ...
    def VolumeStepDown(self, ctx: Any) -> None: ...
    def QueryInterface(self, interface: Any) -> Any: ...

class IAudioMeterInformation:
    """Stub for the COM `IAudioMeterInformation` interface."""

    _iid_: Any
    def GetPeakValue(self) -> float: ...
    def QueryInterface(self, interface: Any) -> Any: ...

class ISimpleAudioVolume:
    """Stub for the COM `ISimpleAudioVolume` interface (per-session)."""
    def GetMasterVolume(self) -> float: ...
    def SetMasterVolume(self, level: float, ctx: Any) -> None: ...
    def GetMute(self) -> int: ...
    def SetMute(self, muted: int, ctx: Any) -> None: ...

class AudioSession:
    """Stub for `pycaw.pycaw.AudioSession`."""

    Process: Any
    SimpleAudioVolume: Any
    _ctl: Any
    def __init__(self) -> None: ...
