# Windows PowerShell 5.1; changes apply only to this installation/current user.
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$script:ReleaseRoot = Split-Path -Parent $PSScriptRoot

function Get-InstallationDirectory {
    param([string]$Source, [string]$LocalAppData)
    foreach ($name in @('.env', 'instance', '.venv', 'model_files')) {
        if (Test-Path -LiteralPath (Join-Path $Source $name)) { return $Source }
    }
    return Join-Path $LocalAppData 'AbsensiWajahKantor'
}

function Get-VerifiedDownload {
    param([string]$Uri, [string]$Destination, [string]$Sha256)
    if ((Test-Path -LiteralPath $Destination) -and
        (Get-FileHash -LiteralPath $Destination -Algorithm SHA256).Hash -eq $Sha256) {
        return
    }
    $partial = $Destination + '.partial'
    try {
        Invoke-WebRequest -Uri $Uri -OutFile $partial -UseBasicParsing -TimeoutSec 120
        if ((Get-FileHash -LiteralPath $partial -Algorithm SHA256).Hash -ne $Sha256) {
            throw 'SHA256 unduhan tidak sesuai; pemasangan dihentikan. Coba lagi dari rilis resmi.'
        }
        Move-Item -LiteralPath $partial -Destination $Destination -Force
    } finally {
        if (Test-Path -LiteralPath $partial) { Remove-Item -LiteralPath $partial -Force }
    }
}

function Install-VCRuntime {
    param([string]$CacheDirectory)
    $path = Join-Path $CacheDirectory 'vc_redist.x64.exe'
    Invoke-WebRequest -Uri 'https://aka.ms/vc14/vc_redist.x64.exe' -OutFile $path -UseBasicParsing -TimeoutSec 120
    $signature = Get-AuthenticodeSignature -FilePath $path
    if ($signature.Status -ne 'Valid' -or $null -eq $signature.SignerCertificate -or
        $signature.SignerCertificate.Subject -notmatch '(^|,\s*)O=Microsoft Corporation(,|$)') {
        throw 'Tanda tangan Microsoft pada runtime Visual C++ tidak valid; pemasangan dihentikan.'
    }
    $version = [regex]::Match((Get-Item -LiteralPath $path).VersionInfo.FileVersion, '^\d+\.\d+\.\d+(\.\d+)?').Value
    if (-not $version) { throw 'Versi installer Visual C++ tidak dapat diverifikasi.' }
    $installed = Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\VisualStudio\14.0\VC\Runtimes\x64' -ErrorAction SilentlyContinue
    if ($null -ne $installed -and $installed.Installed -eq 1 -and
        [version]($installed.Version -replace '^v', '') -ge [version]$version) { return }
    Write-Host 'Windows dapat meminta izin administrator (UAC) untuk runtime Visual C++.'
    try {
        $process = Start-Process -FilePath $path -ArgumentList '/install', '/quiet', '/norestart' -Verb RunAs -Wait -PassThru
    } catch {
        throw 'Pemasangan Visual C++ memerlukan persetujuan UAC. Jalankan pemasangan lagi setelah izin tersedia.'
    }
    if ($process.ExitCode -eq 3010 -or $process.ExitCode -eq 1641) {
        throw 'Runtime Visual C++ memerlukan restart Windows. Setelah login, jalankan pemasangan lagi.'
    }
    if ($process.ExitCode -notin @(0, 1638)) {
        throw "Pemasangan Visual C++ gagal (kode $($process.ExitCode)); aplikasi belum dijalankan."
    }
}

function Set-LoginShortcut {
    param([string]$Root, [string]$StartupDirectory)
    if (-not $StartupDirectory) { throw 'Folder Startup pengguna tidak tersedia.' }
    $shell = New-Object -ComObject WScript.Shell
    $shortcut = $shell.CreateShortcut((Join-Path $StartupDirectory 'Absensi Wajah Kantor.lnk'))
    $shortcut.TargetPath = Join-Path $Root '.venv/Scripts/python.exe'
    $shortcut.Arguments = '-m maintenance.windows_launcher'
    $shortcut.WorkingDirectory = $Root
    $shortcut.WindowStyle = 1
    $shortcut.Description = 'Peluncur lokal absensi; dimulai setelah pengguna login Windows.'
    $shortcut.Save()
}

function Set-PrivateDirectory {
    param([string]$Path)
    New-Item -ItemType Directory -Path $Path -Force | Out-Null
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $system = [Security.Principal.SecurityIdentifier]::new('S-1-5-18')
    $acl = [Security.AccessControl.DirectorySecurity]::new()
    $acl.SetOwner($identity.User)
    $acl.SetAccessRuleProtection($true, $false)
    foreach ($sid in @($identity.User, $system)) {
        $rule = [Security.AccessControl.FileSystemAccessRule]::new(
            $sid, 'FullControl', 'ContainerInherit,ObjectInherit', 'None', 'Allow')
        $acl.AddAccessRule($rule)
    }
    Set-Acl -LiteralPath $Path -AclObject $acl
}

function Get-AppPython {
    param([string]$Root, [string]$CacheDirectory)
    $python = Join-Path $Root '.venv/Scripts/python.exe'
    if (-not (Test-Path -LiteralPath $python)) {
        $archive = Join-Path $CacheDirectory 'uv-0.12.24.zip'
        Get-VerifiedDownload 'https://github.com/astral-sh/uv/releases/download/0.12.24/uv-x86_64-pc-windows-msvc.zip' `
            $archive '7c38608c8a18ee137d748a1773053b07ec8f3a30fab49aebaa6f4e4efeceb019'
        Expand-Archive -LiteralPath $archive -DestinationPath (Join-Path $CacheDirectory 'uv') -Force
        $uv = Join-Path $CacheDirectory 'uv/uv.exe'
        $runtime = Join-Path $Root '.runtime'
        $env:UV_PYTHON_INSTALL_DIR = $runtime
        # Pinned uv metadata verifies the CPython archive SHA256 before installation.
        & $uv python install cpython-3.11.17-windows-x86_64-none --install-dir $runtime --no-bin --no-registry | Out-Host
        if ($LASTEXITCODE -ne 0) { throw 'Unduhan runtime Python belum selesai. Jalankan pemasangan lagi.' }
        $python = & $uv python find --managed-python --system 3.11.17
        if ($LASTEXITCODE -ne 0 -or @($python).Count -ne 1) { throw 'Runtime Python privat tidak ditemukan.' }
        $python = $python.Trim()
    }
    & $python -c "import platform,struct,sys; sys.exit(not (sys.implementation.name == 'cpython' and sys.version_info[:2] == (3,11) and sys.version_info[:3] >= (3,11,17) and struct.calcsize('P') == 8 and platform.machine().lower() in ('amd64','x86_64')))"
    if ($LASTEXITCODE -ne 0) {
        throw 'Runtime lama belum kompatibel dengan CPython 3.11.17+ x64. Lingkungan lama dipertahankan; jangan hapus data atau kunci.'
    }
    return $python
}

function Invoke-WindowsBootstrap {
    if ($env:OS -ne 'Windows_NT' -or -not [Environment]::Is64BitOperatingSystem -or
        $env:PROCESSOR_ARCHITECTURE -ne 'AMD64') {
        throw 'Pemasangan ini memerlukan Windows 10/11 x64 dan PowerShell 64-bit.'
    }
    if (-not $env:LOCALAPPDATA) { throw 'Folder aplikasi privat pengguna tidak tersedia.' }
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    $root = Get-InstallationDirectory $script:ReleaseRoot $env:LOCALAPPDATA
    Set-PrivateDirectory $root
    Set-PrivateDirectory (Join-Path $root 'instance')
    $cache = Join-Path $root '.bootstrap-cache'
    New-Item -ItemType Directory -Path $cache -Force | Out-Null
    Install-VCRuntime $cache
    $python = Get-AppPython $root $cache
    Push-Location -LiteralPath $script:ReleaseRoot
    try {
        & $python -m maintenance.windows_install $root
        if ($LASTEXITCODE -ne 0) { throw 'Pemasangan dependensi belum selesai. Perbaiki pesan di atas lalu jalankan lagi.' }
    } finally { Pop-Location }
    Set-LoginShortcut $root ([Environment]::GetFolderPath('Startup'))
    Push-Location -LiteralPath $root
    try {
        & (Join-Path $root '.venv/Scripts/python.exe') -m maintenance.windows_launcher
        if ($LASTEXITCODE -ne 0) { throw 'Aplikasi belum dapat dibuka. Periksa pesan peluncur lalu jalankan kembali.' }
    } finally { Pop-Location }
}

if ($MyInvocation.InvocationName -ne '.') {
    try { Invoke-WindowsBootstrap } catch {
        Write-Host $_.Exception.Message -ForegroundColor Red
        exit 1
    }
}
