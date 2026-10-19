#ifndef AppVersion
  #error AppVersion is required
#endif
[Setup]
AppId={{7C70B17D-2F2E-452D-B7E8-2135BDA66B77}
AppName=Absensi Wajah Kantor
AppVersion={#AppVersion}
DefaultDirName={localappdata}\AbsensiWajahKantor
DisableDirPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64os
ArchitecturesInstallIn64BitMode=x64os
MinVersion=10.0
OutputDir=..\dist\release
OutputBaseFilename=AbsensiSetup
Compression=lzma2
SolidCompression=yes
CloseApplications=no
RestartApplications=no
UninstallDisplayName=Absensi Wajah Kantor (data perangkat dipertahankan)

[Files]
Source: "..\dist\Absensi\*"; DestDir: "{app}\versions\{#AppVersion}"; Flags: recursesubdirs createallsubdirs ignoreversion
Source: "launch.ps1"; DestDir: "{app}"; Flags: ignoreversion
Source: "prerequisites.ps1"; Flags: dontcopy
Source: "..\build\tools\vc_redist.x64.exe"; Flags: dontcopy

[Icons]
Name: "{userprograms}\Absensi Wajah Kantor"; Filename: "{sys}\WindowsPowerShell\v1.0\powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\launch.ps1"""; WorkingDir: "{app}"
Name: "{userstartup}\Absensi Wajah Kantor"; Filename: "{sys}\WindowsPowerShell\v1.0\powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\launch.ps1"""; WorkingDir: "{app}"

[Run]
Filename: "{sys}\WindowsPowerShell\v1.0\powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\launch.ps1"""; Description: "Buka aplikasi dan wizard setup"; Flags: postinstall nowait skipifsilent; Check: InstallationReady

[UninstallDelete]
Type: filesandordirs; Name: "{app}\versions"
Type: files; Name: "{app}\current.json"

[Code]
var Ready: Boolean;
    LauncherHandle, UpdateHandle: THandle;

function OpenExclusiveFile(Name: String; Access, Share: Cardinal; Security: Integer;
  Creation, Flags: Cardinal; Template: THandle): THandle;
  external 'CreateFileW@kernel32.dll stdcall';
function CloseFileHandle(Handle: THandle): Boolean;
  external 'CloseHandle@kernel32.dll stdcall';

function InstallationReady: Boolean;
begin
  Result := Ready;
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var Code: Integer; PowerShell, Args: String;
begin
  Result := '';
  if FileExists(ExpandConstant('{app}\current.json')) or
     FileExists(ExpandConstant('{app}\.env')) or
     DirExists(ExpandConstant('{app}\.venv')) or
     DirExists(ExpandConstant('{app}\.runtime')) or
     DirExists(ExpandConstant('{app}\model_files')) or
     DirExists(ExpandConstant('{app}\instance')) then begin
    Result := 'Instalasi/data lama ditemukan. Gunakan pembaruan melalui admin lokal; jangan menimpa data perangkat.';
    Exit;
  end;
  ExtractTemporaryFile('prerequisites.ps1');
  ExtractTemporaryFile('vc_redist.x64.exe');
  PowerShell := ExpandConstant('{sys}\WindowsPowerShell\v1.0\powershell.exe');
  Args := ExpandConstant('-NoProfile -ExecutionPolicy Bypass -File "{tmp}\prerequisites.ps1" -Installer "{tmp}\vc_redist.x64.exe" -Root "{app}"');
  if not Exec(PowerShell, Args, '', SW_SHOW, ewWaitUntilTerminated, Code) then
    Result := 'Pemeriksaan runtime tidak dapat dijalankan.'
  else if Code = 3010 then
    Result := 'Restart Windows, lalu jalankan installer kembali. Aplikasi belum dibuka.'
  else if Code <> 0 then
    Result := 'Runtime atau izin penyimpanan belum siap. Periksa UAC dan jalankan installer kembali.';
end;

procedure CurStepChanged(CurStep: TSetupStep);
var Code: Integer;
begin
  if CurStep = ssPostInstall then begin
    if not Exec(ExpandConstant('{app}\versions\{#AppVersion}\Absensi.exe'),
       ExpandConstant('--root "{app}" initialize'), ExpandConstant('{app}'),
       SW_HIDE, ewWaitUntilTerminated, Code) then
      RaiseException('Inisialisasi gagal. Data tidak dihapus.');
    if Code <> 0 then RaiseException('Inisialisasi gagal. Periksa instalasi/data lama.');
    Ready := True;
  end;
end;

function InitializeUninstall: Boolean;
begin
  LauncherHandle := OpenExclusiveFile(ExpandConstant('{app}\instance\launcher.lock'), $C0000000, 0, 0, 4, $80, 0);
  UpdateHandle := OpenExclusiveFile(ExpandConstant('{app}\updates\update.lock'), $C0000000, 0, 0, 4, $80, 0);
  Result := (LauncherHandle <> THandle(-1)) and (UpdateHandle <> THandle(-1));
  if not Result then MsgBox('Tutup aplikasi dan tunggu pembaruan selesai sebelum uninstall. Data perangkat dipertahankan.', mbError, MB_OK);
end;

procedure DeinitializeUninstall;
begin
  if LauncherHandle <> THandle(-1) then CloseFileHandle(LauncherHandle);
  if UpdateHandle <> THandle(-1) then CloseFileHandle(UpdateHandle);
end;
