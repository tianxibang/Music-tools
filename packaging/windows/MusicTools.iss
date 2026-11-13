; Windows installer (Inno Setup 6), built by CI after build.py:
;   iscc /DAppVersion=4.2 packaging\windows\MusicTools.iss   ->  MusicTools-setup-windows-x64.exe
; Installs per user (no admin rights), so the in-app updater can run the next setup silently:
;   setup.exe /SILENT /SUPPRESSMSGBOXES /CLOSEAPPLICATIONS /relaunch=1

#ifndef AppVersion
  #define AppVersion "0.0"
#endif

[Setup]
AppId={{A5AB3BDD-365C-497C-893D-E8593E6725BC}
AppName=Music Tools
AppVersion={#AppVersion}
AppVerName=Music Tools {#AppVersion}
AppPublisher=tianxibang
AppPublisherURL=https://github.com/tianxibang/Music-tools
AppUpdatesURL=https://github.com/tianxibang/Music-tools/releases
DefaultDirName={localappdata}\Programs\Music Tools
DisableProgramGroupPage=yes
DisableDirPage=auto
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=..\..
OutputBaseFilename=MusicTools-setup-windows-x64
SetupIconFile=..\..\assets\icon.ico
UninstallDisplayIcon={app}\MusicTools.exe
UninstallDisplayName=Music Tools
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
CloseApplications=force
RestartApplications=no

[Tasks]
Name: desktopicon; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[InstallDelete]
; The bundled libraries are replaced as a whole, so none of an older version's stay behind
Type: filesandordirs; Name: "{app}\_internal"

[Files]
Source: "..\..\dist\MusicTools\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\Music Tools"; Filename: "{app}\MusicTools.exe"
Name: "{autodesktop}\Music Tools"; Filename: "{app}\MusicTools.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\MusicTools.exe"; Description: "{cm:LaunchProgram,Music Tools}"; Flags: nowait postinstall skipifsilent
; An update from inside the app: start it again when done
Filename: "{app}\MusicTools.exe"; Flags: nowait; Check: Relaunch

[UninstallDelete]
Type: filesandordirs; Name: "{app}\.update"

[Code]
function Relaunch: Boolean;
begin
  Result := ExpandConstant('{param:relaunch|0}') = '1';
end;
