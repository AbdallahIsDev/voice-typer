; Inno Setup script for Voice Typer
; Build: iscc scripts\build\installer.iss

#define MyAppName "Voice Typer"
#define MyAppVersion "1.0.0"
#define MyAppPublisher "AbdallahIsDev"
#define MyAppURL "https://github.com/AbdallahIsDev/voice-typer"
#define MyAppExeName "VoiceTyper.exe"
#define MyBuildDir "..\..\dist"

[Setup]
AppId={{8E6F5B1A-2C3D-4A5E-9B7C-1D2E3F4A5B6C}}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
AppUpdatesURL={#MyAppURL}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
AllowNoIcons=yes
OutputDir=..\..
OutputBaseFilename=VoiceTyper-Setup-{#MyAppVersion}
Compression=lzma
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=lowest
DisableProgramGroupPage=yes
UninstallDisplayIcon={app}\{#MyAppExeName}
; NEW-SEC-018: enforce minimum Windows version and architecture.
; MinVersion=10.0 prevents install on Windows 7/8/8.1 (unsupported).
; ArchitecturesAllowed=x64compatible prevents install on ARM64
; (no native torch wheels) and 32-bit Windows (deprecated).
MinVersion=10.0
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "autostart"; Description: "&Launch on Windows startup"; GroupDescription: "Startup options:"; Flags: checkedonce

[Files]
; NEW-SEC-018: use confirmoverwrite instead of ignoreversion so a
; tampered installer with a lower file version can't silently replace
; a running app. confirmoverwrite prompts the user before overwriting.
Source: "{#MyBuildDir}\{#MyAppExeName}"; DestDir: "{app}"; Flags: confirmoverwrite
Source: "{#MyBuildDir}\*"; DestDir: "{app}"; Flags: confirmoverwrite recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\{#MyAppExeName}"
Name: "{group}\Uninstall {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{userdesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\{#MyAppExeName}"; Comment: "Voice Typer — background voice-to-text"

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Launch {#MyAppName}"; Flags: postinstall nowait skipifsilent shellexec

[Registry]
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "VoiceTyper"; ValueData: """{app}\{#MyAppExeName}"""; Flags: uninsdeletevalue; Tasks: autostart
