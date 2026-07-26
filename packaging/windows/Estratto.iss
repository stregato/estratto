#define MyAppName "Estratto"
#define MyAppVersion "0.1.0"
#define MyAppPublisher "DoubleCorretto"
#define MyAppExeName "Estratto.exe"

[Setup]
AppId={{C29E2D79-6D68-46D2-A0EA-3E251641E0E5}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
OutputDir=dist\windows-installer
OutputBaseFilename=Estratto-Setup
Compression=lzma
SolidCompression=yes
WizardStyle=modern

[Files]
Source: "dist\Estratto\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Launch {#MyAppName}"; Flags: nowait postinstall skipifsilent
