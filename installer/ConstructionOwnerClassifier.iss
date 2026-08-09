#define MyAppName "施工责任人图片分类器"
#define MyAppVersion "1.1.1"
#define MyAppExeName "ConstructionOwnerClassifier.exe"

[Setup]
AppId={{21F1908A-5A54-4D86-BB6E-76B739A3AA55}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher=ConstructionOwnerClassifier
DefaultDirName={localappdata}\Programs\ConstructionOwnerClassifier
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=..\dist
OutputBaseFilename=施工责任人图片分类器-Setup-1.1.1
SetupIconFile=..\assets\app.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
RestartApplications=no
UsePreviousAppDir=yes
VersionInfoVersion=1.1.1.0
VersionInfoProductName={#MyAppName}
VersionInfoProductVersion={#MyAppVersion}

[Languages]
Name: "chinesesimp"; MessagesFile: "ChineseSimplified.isl"

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "附加快捷方式："; Flags: unchecked

[Files]
Source: "..\dist\ConstructionOwnerClassifier\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "启动 {#MyAppName}"; Flags: nowait postinstall skipifsilent

[Code]
var
  RemoveUserData: Boolean;

function InitializeUninstall(): Boolean;
begin
  Result := True;
  if UninstallSilent then
    RemoveUserData := False
  else
    RemoveUserData := MsgBox(
      '是否同时删除任务记录和设置？' + #13#10 +
      '选择“否”可在以后重新安装时保留这些数据。',
      mbConfirmation, MB_YESNO) = IDYES;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if (CurUninstallStep = usUninstall) and RemoveUserData then
  begin
    DelTree(ExpandConstant('{localappdata}\ConstructionOwnerClassifier'), True, True, True);
  end;
end;
