"""PowerShell bootstrap checks with controlled Windows/network boundaries."""
import os
from pathlib import Path
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]


def powershell(script):
    executable = os.environ.get('POWERSHELL_EXE') or shutil.which('powershell') or shutil.which('pwsh')
    if executable is None:
        pytest.skip('PowerShell is unavailable; run these checks in Windows packaging CI.')
    path = str(ROOT / 'scripts' / 'pasang_windows.ps1').replace("'", "''")
    # PowerShell 7 paths inherited through Python break Windows PowerShell module autoloading.
    environment = {key: value for key, value in os.environ.items() if key.upper() != 'PSMODULEPATH'}
    return subprocess.run([executable, '-NoProfile', '-NonInteractive', '-Command',
        f"$ErrorActionPreference = 'Stop'; . '{path}'; {script}"],
        capture_output=True, text=True, timeout=30, env=environment)


def test_unverified_runtime_archive_is_rejected_before_use(tmp_path):
    destination = str(tmp_path / 'uv.zip').replace("'", "''")
    result = powershell(f"""
        function Invoke-WebRequest {{
            param($Uri, $OutFile, [switch]$UseBasicParsing, $TimeoutSec)
            [IO.File]::WriteAllText($OutFile, 'untrusted archive')
        }}
        try {{
            Get-VerifiedDownload 'https://example.invalid/uv.zip' '{destination}' ('0' * 64)
            throw 'Unverified download accepted'
        }} catch {{
            if ($_.Exception.Message -notmatch 'SHA256') {{ throw }}
        }}
        if (Test-Path '{destination}') {{ throw 'Unverified archive became executable input' }}
        if (Test-Path '{destination}.partial') {{ throw 'Partial download was retained' }}
    """)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize('existing', [None, '.env', 'instance', '.venv', 'model_files'])
def test_bootstrap_selects_private_folder_or_reuses_existing_installation(tmp_path, existing):
    source = tmp_path / 'release or old install'
    source.mkdir()
    if existing == '.env':
        (source / existing).write_text('PRESERVE=yes')
    elif existing:
        (source / existing).mkdir()
    expected = source if existing else tmp_path / 'local data' / 'AbsensiWajahKantor'
    result = powershell(f"""
        $actual = Get-InstallationDirectory '{source}' '{tmp_path / 'local data'}'
        if ($actual -ne '{expected}') {{ throw 'Installation relocated or private default not selected' }}
    """)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize(('signature', 'exit_code', 'failure', 'installed_version'), [
    ('NotSigned', 0, True, None),
    ('OtherPublisher', 0, True, None),
    ('Valid', 0, False, None),
    ('Valid', 1638, False, None),
    ('Valid', 3010, True, None),
    ('Valid', 1603, True, None),
    ('Valid', -1, True, None),
    ('Valid', 0, False, 'v14.29.30131.0'),
    ('Valid', 0, False, 'v14.50.1.0'),
    ('Valid', 0, False, 'v14.52.1.0'),
])
def test_dll_prerequisite_requires_trusted_publisher_and_successful_uac(
        tmp_path, signature, exit_code, failure, installed_version):
    installed = (f"[pscustomobject]@{{ Installed = 1; Version = '{installed_version}' }}"
                 if installed_version else '$null')
    status = 'Valid' if signature == 'OtherPublisher' else signature
    publisher = 'Another Vendor' if signature == 'OtherPublisher' else 'Microsoft Corporation'
    result = powershell(f"""
        function Get-ItemProperty {{ param($Path, $ErrorAction); return {installed} }}
        function Get-Item {{
            param($LiteralPath)
            return [pscustomobject]@{{ VersionInfo = [pscustomobject]@{{FileVersion = '14.50.1.0'}} }}
        }}
        function Invoke-WebRequest {{
            param($Uri, $OutFile, [switch]$UseBasicParsing, $TimeoutSec)
            [IO.File]::WriteAllText($OutFile, 'test Microsoft installer')
        }}
        function Get-AuthenticodeSignature {{
            param($FilePath)
            return [pscustomobject]@{{ Status = '{status}';
                SignerCertificate = [pscustomobject]@{{Subject = 'CN={publisher}, O={publisher}, C=US'}} }}
        }}
        function Start-Process {{
            param($FilePath, $ArgumentList, $Verb, [switch]$Wait, [switch]$PassThru)
            [IO.File]::WriteAllText('{tmp_path / 'executed'}', 'yes')
            if ({exit_code} -eq -1) {{ throw 'UAC cancelled' }}
            return [pscustomobject]@{{ ExitCode = {exit_code} }}
        }}
        $failed = $false
        try {{ Install-VCRuntime '{tmp_path}' }} catch {{ $failed = $true }}
        if ($failed -ne ${str(failure).lower()}) {{ throw 'DLL prerequisite failure was hidden or success rejected' }}
    """)
    assert result.returncode == 0, result.stdout + result.stderr
    assert (tmp_path / 'executed').exists() == (
        signature == 'Valid' and installed_version not in ('v14.50.1.0', 'v14.52.1.0'))


def test_login_shortcut_is_single_and_uses_private_runtime_and_working_directory(tmp_path):
    root, startup = tmp_path / 'installed app', tmp_path / 'Startup'
    root.mkdir()
    startup.mkdir()
    result = powershell(f"""
        function New-Object {{
            param($ComObject)
            $shell = [pscustomobject]@{{}}
            $shell | Add-Member ScriptMethod CreateShortcut {{
                param($path)
                $link = [pscustomobject]@{{ Path = $path; TargetPath = ''; Arguments = '';
                    WorkingDirectory = ''; WindowStyle = 1; Description = '' }}
                $link | Add-Member ScriptMethod Save {{
                    $this | ConvertTo-Json | Set-Content -LiteralPath $this.Path
                }}
                return $link
            }}
            return $shell
        }}
        Set-LoginShortcut '{root}' '{startup}'
        Set-LoginShortcut '{root}' '{startup}'
        $links = @(Get-ChildItem -LiteralPath '{startup}' -Filter '*.lnk')
        if ($links.Count -ne 1) {{ throw 'Duplicate login shortcuts' }}
        $link = Get-Content -LiteralPath $links[0].FullName | ConvertFrom-Json
        if ($link.TargetPath -ne (Join-Path '{root}' '.venv/Scripts/python.exe')) {{ throw 'Global runtime selected' }}
        if ($link.Arguments -ne '-m maintenance.windows_launcher') {{ throw 'Launcher not selected' }}
        if ($link.WorkingDirectory -ne '{root}') {{ throw 'Relative data paths would change' }}
    """)
    assert result.returncode == 0, result.stdout + result.stderr


def test_packaged_bootstrap_pins_patched_x64_runtime_without_global_installation():
    script = (ROOT / 'scripts' / 'pasang_windows.ps1').read_bytes().decode('ascii')
    assert 'https://github.com/astral-sh/uv/releases/download/0.12.24/uv-x86_64-pc-windows-msvc.zip' in script
    assert '7c38608c8a18ee137d748a1773053b07ec8f3a30fab49aebaa6f4e4efeceb019' in script
    assert 'cpython-3.11.17-windows-x86_64-none' in script
    assert '--no-bin' in script and '--no-registry' in script
    assert 'Set-ExecutionPolicy' not in script
    assert '3.11.9' not in script


@pytest.mark.skipif(os.name == 'nt', reason='This negative case must never start Windows installation.')
def test_non_windows_bootstrap_refuses_before_downloading_or_installing(tmp_path):
    result = powershell(f"$env:LOCALAPPDATA = '{tmp_path}'; Invoke-WindowsBootstrap")
    assert result.returncode != 0
    assert 'Windows 10/11 x64' in result.stdout + result.stderr
    assert list(tmp_path.iterdir()) == []
