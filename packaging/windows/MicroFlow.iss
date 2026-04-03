#define MyAppName "MicroFlow"
#ifndef MyAppVersion
  #define MyAppVersion "v1.0.0"
#endif
#ifndef MySourceDir
  #define MySourceDir "..\\..\\dist\\MicroFlow"
#endif
#ifndef MyOutputDir
  #define MyOutputDir "..\\..\\release\\windows"
#endif

[Setup]
AppId={{A78D7E70-8B65-47B8-8D44-9E05D9EFC1C1}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
DefaultDirName={autopf}\MicroFlow
DefaultGroupName=MicroFlow
UninstallDisplayIcon={app}\MicroFlow.exe
SetupIconFile=..\..\frontend\icons\icon.ico
OutputDir={#MyOutputDir}
OutputBaseFilename=MicroFlow-Setup-{#MyAppVersion}
Compression=lzma
SolidCompression=yes
WizardStyle=modern
ArchitecturesInstallIn64BitMode=x64compatible
DisableProgramGroupPage=yes

[Languages]
Name: "chinesesimp"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "附加任务:"

[Files]
Source: "{#MySourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\MicroFlow"; Filename: "{app}\MicroFlow.exe"
Name: "{commondesktop}\MicroFlow"; Filename: "{app}\MicroFlow.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\MicroFlow.exe"; Description: "启动 MicroFlow"; Flags: nowait postinstall skipifsilent
