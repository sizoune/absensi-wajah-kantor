param([switch]$TestKey)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
if (-not [Environment]::Is64BitProcess -or $env:OS -ne 'Windows_NT') { throw 'Build pada Windows x64.' }
& python -c "import sys,struct; assert sys.version_info[:3] == (3,11,17) and struct.calcsize('P') == 8"
if ($LASTEXITCODE -ne 0) { throw 'Build memerlukan CPython 3.11.17 x64.' }
& python -m pip check
if ($LASTEXITCODE -ne 0) { throw 'Dependensi build tidak konsisten.' }
New-Item -ItemType Directory -Path 'build/tools' -Force | Out-Null
$tools = Get-Content 'distribution/build-tools.json' -Raw | ConvertFrom-Json
foreach ($entry in @(@('inno', $tools.inno, 'innosetup.exe'), @('vc', $tools.vc_redist, 'vc_redist.x64.exe'))) {
    $target = Join-Path $root ('build/tools/' + $entry[2])
    Invoke-WebRequest -Uri $entry[1].url -OutFile $target -UseBasicParsing -TimeoutSec 180
    if ((Get-FileHash -LiteralPath $target -Algorithm SHA256).Hash.ToLowerInvariant() -ne $entry[1].sha256) { throw 'Hash alat build tidak sesuai.' }
    $signature = Get-AuthenticodeSignature -LiteralPath $target
    if ($signature.Status -ne 'Valid' -or $null -eq $signature.SignerCertificate -or
        -not $signature.SignerCertificate.Subject.Contains($entry[1].publisher)) { throw 'Signature alat build tidak valid.' }
}
$inno = Join-Path $root 'build/tools/inno'
$process = Start-Process -FilePath 'build/tools/innosetup.exe' -ArgumentList '/VERYSILENT','/SUPPRESSMSGBOXES','/NORESTART',('/DIR="' + $inno + '"') -Wait -PassThru
if ($process.ExitCode -ne 0) { throw 'Pemasangan compiler Inno gagal.' }
if ($TestKey) { & python scripts/prepare_windows_build.py --test-key }
else { & python scripts/prepare_windows_build.py }
if ($LASTEXITCODE -ne 0) { throw 'Persiapan build gagal.' }
& python -m PyInstaller --clean --noconfirm distribution/Absensi.spec
if ($LASTEXITCODE -ne 0) { throw 'Build executable gagal.' }
$scratch = Join-Path $root 'build/smoke-instance'
if (Test-Path $scratch) { Remove-Item -LiteralPath $scratch -Recurse -Force }
& dist/Absensi/Absensi.exe --root $scratch self-check --fixtures build/inference
if ($LASTEXITCODE -ne 0) { throw 'Self-check executable/inferensi gagal.' }
$version = & python -c 'from app_version import VERSION; print(VERSION)'
& (Join-Path $inno 'ISCC.exe') "/DAppVersion=$version" distribution/installer.iss
if ($LASTEXITCODE -ne 0) { throw 'Build installer gagal.' }
& python -c "import shutil; shutil.make_archive('dist/release/windows-x64', 'zip', 'dist/Absensi')"
if ($LASTEXITCODE -ne 0) { throw 'Pembuatan paket update gagal.' }
