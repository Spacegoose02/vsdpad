; Inno Setup script for VSDPad-Setup-<version>.exe. Built by installer\build.ps1 / the GitHub workflow.
; Installs for the current user only (no admin prompt) into %LOCALAPPDATA%\Programs\VSD Pad.
; Your layout lives in %APPDATA%\VSD Pad and is never removed by updating or uninstalling.

#ifndef AppVersion
  #define AppVersion "2.0.0"
#endif
#ifndef NumVersion
  #define NumVersion "2.0.0.0"
#endif
#ifndef SourceDir
  #define SourceDir "..\build\dist\VSDPad"
#endif
#ifndef OutputDir
  #define OutputDir "..\build"
#endif

[Setup]
AppId={{7D3B1E0A-5C2F-4C1B-9B7E-56A0D5C3F2A9}
AppName=VSD Pad
AppVersion={#AppVersion}
AppPublisher=Spacegoose02
; version details on the setup file (Properties -> Details): unsigned files without them look more suspicious
VersionInfoVersion={#NumVersion}
VersionInfoProductVersion={#NumVersion}
VersionInfoProductTextVersion={#AppVersion}
VersionInfoCompany=Spacegoose02
VersionInfoCopyright=Spacegoose02
VersionInfoDescription=VSD Pad setup
VersionInfoProductName=VSD Pad
DefaultDirName={localappdata}\Programs\VSD Pad
DefaultGroupName=VSD Pad
DisableProgramGroupPage=yes
DisableDirPage=yes
PrivilegesRequired=lowest
OutputDir={#OutputDir}
OutputBaseFilename=VSDPad-Setup-{#AppVersion}
SetupIconFile=..\vsdpad\web\vsdpad.ico
UninstallDisplayIcon={app}\VSDPad.exe
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesInstallIn64BitMode=x64compatible
; the old and new app both hold this while running: setup asks you to quit VSD Pad first
AppMutex=Local\VSDPadController
CloseApplications=yes

[Tasks]
Name: "startup"; Description: "Start VSD Pad when I sign in to Windows"; GroupDescription: "Options:"
Name: "desktopicon"; Description: "Put a VSD Pad icon on my desktop"; GroupDescription: "Options:"; Flags: unchecked

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\VSD Pad"; Filename: "{app}\VSDPad.exe"; Parameters: "--gui"; Comment: "Start VSD Pad and open its settings"
Name: "{group}\Uninstall VSD Pad"; Filename: "{uninstallexe}"
Name: "{userdesktop}\VSD Pad"; Filename: "{app}\VSDPad.exe"; Parameters: "--gui"; Tasks: desktopicon
; same name as the Settings switch uses, so the switch shows it as on
Name: "{userstartup}\VSD Pad"; Filename: "{app}\VSDPad.exe"; WorkingDir: "{app}"; Comment: "VSD Pad (starts with Windows)"; Tasks: startup

[Run]
Filename: "{app}\VSDPad.exe"; Parameters: "--gui"; Description: "Start VSD Pad now"; Flags: nowait postinstall skipifsilent
; quiet update from inside the app (Plugins -> Updates): start it again, without opening the settings page
Filename: "{app}\VSDPad.exe"; Flags: nowait; Check: WizardSilent

[UninstallDelete]
Type: files; Name: "{userstartup}\VSD Pad.lnk"
