[Setup]
AppName=Audify
AppVersion=2.2.4
AppPublisher=Sheikh Technologies
DefaultDirName={autopf}\Audify
DefaultGroupName=Audify
UninstallDisplayIcon={app}\logo.ico
Compression=lzma2
SolidCompression=yes
OutputDir=Output
OutputBaseFilename=Setup_Audify_2.2.4
SetupIconFile=logo.ico
ArchitecturesInstallIn64BitMode=x64
DisableProgramGroupPage=yes
PrivilegesRequired=lowest

[Files]
Source: "Audify.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "logo.ico"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\Audify"; Filename: "{app}\Audify.exe"; IconFilename: "{app}\logo.ico"
Name: "{autodesktop}\Audify"; Filename: "{app}\Audify.exe"; IconFilename: "{app}\logo.ico"; Tasks: desktopicon
Name: "{userstartup}\Audify"; Filename: "{app}\Audify.exe"; Tasks: startupicon

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Additional icons:"
Name: "startupicon"; Description: "Run Audify automatically when Windows starts"; GroupDescription: "Startup options:"

[Run]
Filename: "{app}\Audify.exe"; Description: "Launch Audify now"; Flags: nowait postinstall skipifsilent
