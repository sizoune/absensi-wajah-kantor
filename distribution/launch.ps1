param([switch]$Recover, [switch]$ResetAdminPassword)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot
try {
    $current = Get-Content -LiteralPath (Join-Path $root 'current.json') -Raw | ConvertFrom-Json
    if ($current.version -notmatch '^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$') {
        throw 'Versi aktif tidak valid.'
    }
    $executable = Join-Path $root ('versions\' + $current.version + '\Absensi.exe')
    $mode = 'launch'
    if ($Recover) { $mode = 'recover' }
    if ($ResetAdminPassword) { $mode = 'reset-admin-password' }
    & $executable --root $root $mode
    if ($LASTEXITCODE -ne 0) { throw 'Operasi belum selesai. Lihat panduan pemulihan distribusi.' }
} catch {
    Write-Host 'Aplikasi belum dapat dibuka. Data lama dipertahankan.'
    Write-Host 'Periksa aplikasi lain, port 5000, runtime, dan status pemulihan lokal.'
    Read-Host 'Tekan Enter untuk menutup'
    exit 1
}
