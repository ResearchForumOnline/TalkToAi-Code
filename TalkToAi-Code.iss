; Standard Inno Setup installer for TalkToAi Code.
; The application keeps task history/configuration separately in
; %LOCALAPPDATA%\TalkToAiCode, so upgrades/uninstalls do not erase it.

#define AppName "TalkToAi Code"
#define AppVersion "0.4.1"
#define AppExeName "TalkToAiCode.exe"

[Setup]
AppId={{E5B24768-C67D-482E-8F4C-61A928C75A05}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher=TalkToAI
AppPublisherURL=https://talktoai.org/TALKTOAIcode/
AppSupportURL=https://github.com/ResearchForumOnline/TalkToAi-Code/issues
DefaultDirName={localappdata}\Programs\TalkToAi Code
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
OutputDir=dist-installer
OutputBaseFilename=TalkToAi-Code-0.4.1-Windows-Setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayIcon={app}\{#AppExeName}

[Files]
Source: "dist-studio-v6\TalkToAiCode\*"; DestDir: "{app}"; Excludes: "studio-preview.png,packaged-release-check.json"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExeName}"; WorkingDir: "{app}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExeName}"; WorkingDir: "{app}"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Additional shortcuts:"

[Run]
Filename: "{app}\{#AppExeName}"; Description: "Launch {#AppName}"; Flags: nowait postinstall skipifsilent
