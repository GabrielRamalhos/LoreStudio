$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$required = @('packaging/lore_oculta.spec', 'packaging/installer.iss', 'packaging/THIRD_PARTY_NOTICES.txt', 'packaging/COMPONENT_MANIFEST.json', 'packaging/DISTRIBUTION.md', 'packaging/EULA.md', 'packaging/PRIVACY.md', 'packaging/COMMERCIAL_POLICY.md', 'packaging/EXTERNAL_RELEASE_CHECKLIST.md', 'packaging/vendor/ffmpeg/ffmpeg.exe', 'packaging/vendor/kokoro')
foreach ($item in $required) { if (-not (Test-Path $item)) { throw "Arquivo obrigatório ausente: $item" } }

$manifestPath = 'packaging/COMPONENT_MANIFEST.json'
$manifest = Get-Content $manifestPath -Raw | ConvertFrom-Json
$pyprojectVersion = ([regex]::Match((Get-Content 'pyproject.toml' -Raw), '(?m)^version\s*=\s*"([^"]+)"')).Groups[1].Value
if ([string]::IsNullOrWhiteSpace($pyprojectVersion)) { throw 'Versão do projeto não encontrada em pyproject.toml.' }
if ($manifest.version -ne $pyprojectVersion) { throw "Versão do manifesto ($($manifest.version)) diverge de pyproject.toml ($pyprojectVersion)." }
foreach ($component in $manifest.components) {
  foreach ($field in @('name', 'version', 'origin', 'license', 'path')) {
    if ([string]::IsNullOrWhiteSpace([string]$component.$field) -or $component.$field -eq 'audit-required') { throw "Componente auditado incompleto ($field): $($component.name)" }
  }
  if (-not (Test-Path $component.path)) { throw "Entrada auditada ausente: $($component.path)" }
}
if (-not (Get-Command pyinstaller -ErrorAction SilentlyContinue)) { throw 'PyInstaller não encontrado. Instale as dependências de build na máquina preparada.' }
if (-not (Get-Command iscc -ErrorAction SilentlyContinue)) { throw 'Inno Setup (iscc) não encontrado na máquina preparada.' }

$ffmpegConfig = & 'packaging/vendor/ffmpeg/ffmpeg.exe' -buildconf 2>&1 | Out-String
if ($ffmpegConfig -match '--enable-gpl') { throw 'FFmpeg GPL detectado; use somente binário LGPL auditado.' }
$forbidden = @('models/piper', '.venv', 'projects', 'lore_oculta.db', 'config/settings.json')
foreach ($item in $forbidden) { if (Test-Path $item) { Write-Host "Excluído do artefato: $item" } }

function Get-ComponentHash([string]$Path) {
  if (Test-Path $Path -PathType Leaf) { return (Get-FileHash -Algorithm SHA256 -LiteralPath $Path).Hash.ToLowerInvariant() }
  $hashes = Get-ChildItem -LiteralPath $Path -Recurse -File | Sort-Object FullName | ForEach-Object { (Get-FileHash -Algorithm SHA256 -LiteralPath $_.FullName).Hash.ToLowerInvariant() }
  if (-not $hashes) { throw "Entrada auditada vazia: $Path" }
  $joined = [Text.Encoding]::UTF8.GetBytes(($hashes -join "`n"))
  return ([Security.Cryptography.SHA256]::HashData($joined) | ForEach-Object { $_.ToString('x2') }) -join ''
}
foreach ($component in $manifest.components) { $component.sha256 = Get-ComponentHash $component.path }
$manifest | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $manifestPath -Encoding utf8
if ((Get-Content $manifestPath -Raw) -match 'generated-at-release') { throw 'Manifesto final ainda contém placeholder.' }

Remove-Item -Recurse -Force dist, build -ErrorAction SilentlyContinue
pyinstaller --noconfirm packaging/lore_oculta.spec
if (Test-Path 'dist/LoreOcultaStudio/models') { throw 'Modelos não aprovados encontrados no artefato.' }
if (Test-Path 'dist/LoreOcultaStudio/piper*') { throw 'Piper não pode integrar o pacote base.' }
iscc "/DAppVersion=$($manifest.version)" packaging/installer.iss
