[Setup]
AppName=Audify
AppVersion=2.8.1
AppPublisher=Sheikh Technologies
DefaultDirName={autopf}\Audify
DefaultGroupName=Audify
UninstallDisplayIcon={app}\logo.ico
Compression=lzma2
SolidCompression=yes
OutputDir=Output
OutputBaseFilename=Setup_Audify_2.8.1
SetupIconFile=logo.ico
ArchitecturesInstallIn64BitMode=x64
DisableProgramGroupPage=yes
PrivilegesRequired=lowest

[Files]
Source: "App\Audify\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
; Offline Kokoro voice model (so offline voices work immediately, no download)
Source: "models\kokoro-v1.0.fp16.onnx"; DestDir: "{app}\models"; Flags: ignoreversion
Source: "models\voices-v1.0.bin"; DestDir: "{app}\models"; Flags: ignoreversion
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
