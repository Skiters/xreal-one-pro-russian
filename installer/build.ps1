$ErrorActionPreference = 'Stop'
Set-Location (Split-Path $PSScriptRoot)
$python = Join-Path (Get-Location) 'work/installer-venv/Scripts/python.exe'
if (-not (Test-Path $python)) {
    python -m venv work/installer-venv
    if ($LASTEXITCODE) { throw 'venv failed' }
}
& $python -m pip install -r installer/requirements.txt
if ($LASTEXITCODE) { throw 'Dependency installation failed' }
& $python -m unittest discover -s installer -p 'test_*.py' -v
if ($LASTEXITCODE) { throw 'Tests failed' }
$firmware = (Resolve-Path 'outputs/xreal-russian-firmware/XREAL_One_Pro_Pilot_RU.bin').Path
$real3d = (Resolve-Path 'outputs/xreal-pilot2-real3d-cpu/XREAL-Pilot2-RU-Hebe-Real3D-CPU.bin').Path
& $python -m PyInstaller --noconfirm --onefile --windowed --noupx --name XREAL-Russian-Updater --paths work --paths installer --distpath outputs/xreal-windows-installer --workpath work/installer-build --specpath work installer/app.py
if ($LASTEXITCODE) { throw 'Build failed' }
$bundle = Join-Path (Get-Location) 'outputs/xreal-windows-installer/bundle'
New-Item -ItemType Directory -Path $bundle -Force | Out-Null
Copy-Item 'outputs/xreal-windows-installer/XREAL-Russian-Updater.exe' (Join-Path $bundle 'XREAL-Russian-Updater.exe') -Force
Copy-Item $firmware (Join-Path $bundle 'XREAL-One-RU-Pilot1.9.1.bin') -Force
Copy-Item $real3d (Join-Path $bundle 'XREAL-Pilot2-RU-Hebe-Real3D-CPU.bin') -Force
Copy-Item 'installer/PACKAGE-README.md' (Join-Path $bundle 'README.md') -Force
$hashes = Get-ChildItem -LiteralPath $bundle -File | ForEach-Object {
    '{0}  {1}' -f (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToLowerInvariant(), $_.Name
}
$hashes | Set-Content -LiteralPath (Join-Path $bundle 'SHA256SUMS.txt') -Encoding ascii
$archive = Join-Path (Get-Location) 'outputs/xreal-windows-installer/XREAL-Russian-Updater-v0.5.0.zip'
Compress-Archive -Path (Join-Path $bundle '*') -DestinationPath $archive -CompressionLevel Optimal -Force
Write-Output "ZIP: $archive"
