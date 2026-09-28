; 王艳霞中医门诊 · Windows 安装程序（Inno Setup 6）
; 先用 PyInstaller 生成 dist\Wenzhen\，再在本目录运行：
;     iscc /DAppVersion=1.1.0 installer.iss
; 若本目录有 MicrosoftEdgeWebview2Setup.exe，安装时会在缺少 WebView2 的电脑上自动安装。

#ifndef AppVersion
  #define AppVersion "1.1.0"
#endif
#define AppName "王艳霞中医门诊"
#define AppExe "Wenzhen.exe"
#define WebView2Setup "MicrosoftEdgeWebview2Setup.exe"

[Setup]
AppId={{6F3D2A91-4C7B-4E1A-9B52-8D0E7C3A1F64}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppName}
VersionInfoVersion={#AppVersion}
VersionInfoProductName={#AppName} 问诊记录系统
DefaultDirName={autopf}\WenzhenClinic
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
OutputDir=..\dist
OutputBaseFilename=WenzhenClinic-Setup-{#AppVersion}
SetupIconFile=assets\icon.ico
UninstallDisplayIcon={app}\{#AppExe}
UninstallDisplayName={#AppName}
CloseApplications=yes
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern

[Languages]
#if FileExists(AddBackslash(CompilerPath) + "Languages\ChineseSimplified.isl")
Name: "chinesesimp"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"
#else
Name: "chinesesimp"; MessagesFile: "ChineseSimplified.isl"
#endif

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
Source: "..\dist\Wenzhen\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
#if FileExists(SourcePath + WebView2Setup)
Source: "{#WebView2Setup}"; DestDir: "{tmp}"; Flags: deleteafterinstall; Check: NeedsWebView2
#endif

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{group}\卸载 {#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Run]
#if FileExists(SourcePath + WebView2Setup)
Filename: "{tmp}\{#WebView2Setup}"; Parameters: "/silent /install"; StatusMsg: "正在安装 Microsoft Edge WebView2 运行库，请稍候…"; Check: NeedsWebView2; Flags: waituntilterminated
#endif
Filename: "{app}\{#AppExe}"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent

[Messages]
; 较新版 Inno Setup 新增的提示（复制文件出错时的“重试/取消”）
chinesesimp.RetryCancelSelectAction=选择操作
chinesesimp.RetryCancelRetry=重试(&T)
chinesesimp.RetryCancelCancel=取消
chinesesimp.FinishedLabel=已完成安装 [name]。%n%n患者数据保存在当前用户的 AppData\Roaming\WenzhenClinic 文件夹中，卸载本软件不会删除这些数据。

[Code]
function WebView2Found(RootKey: Integer; SubKey: String): Boolean;
var
  Version: String;
begin
  Result := RegQueryStringValue(RootKey, SubKey, 'pv', Version)
    and (Version <> '') and (Version <> '0.0.0.0');
end;

{ 检查 Microsoft Edge WebView2 运行库是否已安装（Windows 11 通常已自带） }
function NeedsWebView2: Boolean;
var
  Key: String;
begin
  Key := 'Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}';
  Result := not (WebView2Found(HKLM, 'SOFTWARE\WOW6432Node\' + Key)
    or WebView2Found(HKLM, 'SOFTWARE\' + Key)
    or WebView2Found(HKCU, 'Software\' + Key));
end;
