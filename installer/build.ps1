param([string]$FirmwareDirectory = '', [string]$FirmwareVersion = '', [switch]$PreviewOnly)
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
$version = & $python -c "import sys;sys.path.insert(0,'installer');from version import VERSION;print(VERSION)"
if ($LASTEXITCODE) { throw 'Version resolution failed' }
if ($PreviewOnly) {
    & $python -m PyInstaller --noconfirm --onefile --windowed --noupx --name XREAL-Updater-Windows-Preview --paths work --paths installer --distpath outputs/windows-preview --workpath work/windows-preview-build --specpath work installer/app.py
    if ($LASTEXITCODE) { throw 'Preview build failed' }
    return
}
if (-not $FirmwareVersion) { $FirmwareVersion = $version }
if (-not $FirmwareDirectory) { $FirmwareDirectory = "outputs/v$FirmwareVersion" }
$destination = Join-Path (Get-Location) "outputs/release-$version"
New-Item -ItemType Directory -Path $destination -Force | Out-Null
$bins = @('one-pro','one') | ForEach-Object { Join-Path $FirmwareDirectory "XREAL-$_-Pilot2-RU-$FirmwareVersion.bin" }
$bins += Join-Path $FirmwareDirectory 'XREAL-1S-RU-Pilot1.9.1.bin'
foreach ($bin in $bins) {
    if (-not (Test-Path -LiteralPath $bin)) { throw "Build firmware first: $bin" }
    & $python -c "import sys;sys.path.insert(0,'installer');import backend;print(backend.identify_package(sys.argv[1]).key)" $bin
    if ($LASTEXITCODE) { throw "BIN validation failed: $bin" }
}
& $python -m PyInstaller --noconfirm --onefile --windowed --noupx --name "XREAL-Russian-Updater-$version" --paths work --paths installer --distpath $destination --workpath work/installer-build --specpath work installer/app.py
if ($LASTEXITCODE) { throw 'Build failed' }
foreach ($bin in $bins) { Copy-Item -LiteralPath $bin -Destination $destination -Force }
Copy-Item -LiteralPath 'outputs/xreal-russian-firmware/XREAL_One_Pro_Pilot_RU.bin' -Destination (Join-Path $destination 'XREAL-One-OnePro-RU-Pilot1.9.1.bin') -Force
Copy-Item -LiteralPath (Join-Path $FirmwareDirectory 'build-report.json') -Destination $destination -Force
Copy-Item -LiteralPath 'README.md' -Destination (Join-Path $destination 'README.md') -Force
& $python installer/write_checksums.py $destination
if ($LASTEXITCODE) { throw 'Checksum generation failed' }
Write-Output "Separate EXE and model-specific BINs: $destination"
