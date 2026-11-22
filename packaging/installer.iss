; Inno Setup 6 script for Star ExTractor. Built by packaging\build.ps1, which passes:
;   /DAppVersion=<version from pyproject.toml>  /DSourceDir=<dist\StarExtractor>  /DOutputDir=<dist\release>
; Per-user install by default (no admin rights); the user can choose "all users" in the first page.
; User data (%APPDATA%\StarExtractor: settings, logs; the data root) is never inside the install folder.

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#ifndef SourceDir
  #define SourceDir "..\dist\StarExtractor"
#endif
#ifndef OutputDir
  #define OutputDir "..\dist\release"
#endif
#define AppName "Star ExTractor"
#define AppExe "StarExtractor.exe"
#define AppId "StarExTractor.App"

[Setup]
AppId={{6F3D2B1E-8C4A-4E7B-9C21-5A0E3F6B7D42}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher=Star ExTractor contributors
AppPublisherURL=https://github.com/MBoerlage/Star-ExTractor
AppSupportURL=https://github.com/MBoerlage/Star-ExTractor/issues
AppUpdatesURL=https://github.com/MBoerlage/Star-ExTractor/releases
DefaultDirName={autopf}\Star ExTractor
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
OutputDir={#OutputDir}
OutputBaseFilename=StarExtractor-Setup-{#AppVersion}
SetupIconFile=..\starextractor\resources\starex.ico
UninstallDisplayIcon={app}\{#AppExe}
UninstallDisplayName={#AppName} {#AppVersion}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
LicenseFile=..\LICENSE

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
; AppUserModelID matches the one the app sets, so the taskbar groups the window under this shortcut's icon
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"; AppUserModelID: "{#AppId}"
Name: "{group}\Uninstall {#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon; AppUserModelID: "{#AppId}"

[Run]
Filename: "{app}\{#AppExe}"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent

[Code]
// The uninstaller removes the program; it asks before touching the user's settings and logs:
// %APPDATA%\StarExtractor (current) and %APPDATA%\starex_reduce (before the rename; left in place by the app).
// The data root (Stacks, Masters, Processed, Published) is never touched. Silent uninstall keeps everything.
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  NewDir, OldDir, Shown: string;
begin
  if CurUninstallStep = usPostUninstall then
  begin
    NewDir := ExpandConstant('{userappdata}\StarExtractor');
    OldDir := ExpandConstant('{userappdata}\starex_reduce');
    Shown := '';
    if DirExists(NewDir) then Shown := Shown + NewDir + #13#10;
    if DirExists(OldDir) then Shown := Shown + OldDir + #13#10;
    if Shown <> '' then
      if SuppressibleMsgBox('Also remove your Star ExTractor settings and logs?' + #13#10 + #13#10 + Shown + #13#10 +
                'Your data folder (Stacks, Masters, Processed, Published) is not affected either way.',
                mbConfirmation, MB_YESNO or MB_DEFBUTTON2, IDNO) = IDYES then  // silent: keep the data
      begin
        if DirExists(NewDir) then DelTree(NewDir, True, True, True);
        if DirExists(OldDir) then DelTree(OldDir, True, True, True);
      end;
  end;
end;
