param([string]$NsisCompiler = '', [string]$EnvFile = '.env')
$ErrorActionPreference = 'Stop'
$project = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$python = Join-Path $project '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) { throw 'Crea .venv e instala las dependencias y requirements-build.txt.' }
if (-not $NsisCompiler) {
    $compiler = Get-Command makensis.exe -ErrorAction SilentlyContinue
    if ($compiler) { $NsisCompiler = $compiler.Source }
    else {
        $localCompiler = Join-Path $project '.tools\nsis-3.13\makensis.exe'
        if (Test-Path -LiteralPath $localCompiler) { $NsisCompiler = $localCompiler }
    }
}
if (-not $NsisCompiler -or -not (Test-Path -LiteralPath $NsisCompiler)) { throw 'Indica -NsisCompiler con la ruta a makensis.exe de NSIS.' }
Push-Location -LiteralPath $project
try {
    & $python -m unittest discover -s tests -q
    if ($LASTEXITCODE) { throw 'Fallaron las pruebas.' }
    New-Item -ItemType Directory -Path build, dist -Force | Out-Null
    & $python scripts/prepare_release.py --env-file $EnvFile
    if ($LASTEXITCODE) { throw 'No se pudo validar la dirección del cliente.' }
    @'
import sys
sys.path.insert(0, 'src')
from gekko_sync.desktop import create_icon
create_icon().save('build/gekko.ico')
'@ | & $python -
    if ($LASTEXITCODE) { throw 'No se pudo generar el icono.' }
    & $python -m PyInstaller --noconfirm --onedir --windowed --name GekkoSync --paths src --icon build/gekko.ico --add-data 'build/_build_config.json;gekko_sync' --hidden-import pystray._win32 --hidden-import watchdog.observers.read_directory_changes run.py
    if ($LASTEXITCODE) { throw 'Falló el empaquetado.' }
    & $NsisCompiler '/V2' "installer\GekkoSync.nsi"
    if ($LASTEXITCODE) { throw 'Falló el instalador.' }
    & $python scripts/prepare_release.py --env-file $EnvFile --finalize
    if ($LASTEXITCODE) { throw 'No se pudo generar el manifiesto del instalador.' }
    $releaseHash = Get-FileHash -LiteralPath 'dist\GekkoSync-Setup-0.1.0-x64.exe' -Algorithm SHA256
    ($releaseHash.Hash + '  GekkoSync-Setup-0.1.0-x64.exe') | Set-Content -LiteralPath 'dist\GekkoSync-Setup-0.1.0-x64.sha256' -Encoding ascii
    $releaseHash | Format-List
} finally { Pop-Location }
