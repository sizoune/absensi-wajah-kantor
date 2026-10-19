param([Parameter(Mandatory=$true)][string]$Installer, [Parameter(Mandatory=$true)][string]$Root)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
try {
    if (-not [Environment]::Is64BitOperatingSystem -or -not [Environment]::Is64BitProcess) { throw 'Windows x64 diperlukan.' }
    $signature = Get-AuthenticodeSignature -LiteralPath $Installer
    if ($signature.Status -ne 'Valid' -or $null -eq $signature.SignerCertificate -or
        $signature.SignerCertificate.Subject -notmatch '(^|,\s*)O=Microsoft Corporation(,|$)') { throw 'Signature runtime tidak valid.' }
    $version = [regex]::Match((Get-Item -LiteralPath $Installer).VersionInfo.FileVersion, '^\d+\.\d+\.\d+(\.\d+)?').Value
    if (-not $version) { throw 'Versi runtime tidak valid.' }
    $installed = Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\VisualStudio\14.0\VC\Runtimes\x64' -ErrorAction SilentlyContinue
    if ($null -eq $installed -or $installed.Installed -ne 1 -or [version]($installed.Version -replace '^v','') -lt [version]$version) {
        $process = Start-Process -FilePath $Installer -ArgumentList '/install','/quiet','/norestart' -Verb RunAs -Wait -PassThru
        if ($process.ExitCode -in @(3010, 1641)) { exit 3010 }
        if ($process.ExitCode -notin @(0, 1638)) { throw 'Runtime gagal dipasang.' }
    }
    New-Item -ItemType Directory -Path $Root -Force | Out-Null
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $acl = [Security.AccessControl.DirectorySecurity]::new()
    $acl.SetOwner($identity.User)
    $acl.SetAccessRuleProtection($true, $false)
    foreach ($sid in @($identity.User, [Security.Principal.SecurityIdentifier]::new('S-1-5-18'))) {
        $acl.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new($sid, 'FullControl', 'ContainerInherit,ObjectInherit', 'None', 'Allow'))
    }
    Set-Acl -LiteralPath $Root -AclObject $acl
    exit 0
} catch {
    Write-Host 'Pemasangan runtime/izin belum selesai; data lama dipertahankan.'
    exit 1
}
