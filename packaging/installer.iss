#define AppName "Lore Oculta Studio"
#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#define AppPublisher "Gabriel Ramalhos"
#define AppSupport "gabriel.ramalhos97@gmail.com"

[Setup]
AppId={{9C29C216-E73F-45A7-BC4D-7B1299C01F38}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
AppSupportURL=mailto:{#AppSupport}
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
OutputDir=dist\installer
OutputBaseFilename=LoreOcultaStudio-Setup-{#AppVersion}
Compression=lzma
SolidCompression=yes
ArchitecturesInstallIn64BitMode=x64
DisableProgramGroupPage=yes
LicenseFile=packaging\EULA.md

[Files]
Source: "dist\LoreOcultaStudio\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\LoreOcultaStudio.exe"

[Run]
Filename: "{app}\LoreOcultaStudio.exe"; Description: "Abrir {#AppName}"; Flags: nowait postinstall skipifsilent
