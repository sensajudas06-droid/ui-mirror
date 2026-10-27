param(
    [switch]$SkipPortable
)

$ErrorActionPreference = 'Stop'
$project = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$parent = Split-Path $project -Parent
$dist = Join-Path $project 'dist'
$build = Join-Path $project 'build\packaging'
$bootstrapPython = (Get-Command python -ErrorAction Stop).Source
$venv = Join-Path $project 'build\venv'
$python = Join-Path $venv 'Scripts\python.exe'
$dependencyLock = Join-Path $PSScriptRoot 'requirements-build.lock'
$nsis = 'C:\Program Files (x86)\NSIS\makensis.exe'

if (-not (Test-Path -LiteralPath $nsis)) {
    throw 'NSIS makensis.exe bulunamadı.'
}
if (-not (Test-Path -LiteralPath $dependencyLock)) {
    throw 'Paketleme bağımlılık kilidi bulunamadı: packaging\requirements-build.lock'
}

$environmentNames = @('PATH', 'PYTHONPATH', 'PYTHONHOME', 'TCL_LIBRARY', 'TK_LIBRARY')
$previousEnvironment = @{}
foreach ($name in $environmentNames) {
    $previousEnvironment[$name] = [Environment]::GetEnvironmentVariable($name, 'Process')
}

try {
foreach ($name in @('PYTHONPATH', 'PYTHONHOME', 'TCL_LIBRARY', 'TK_LIBRARY')) {
    if (Test-Path -LiteralPath "Env:$name") {
        Remove-Item -LiteralPath "Env:$name" -ErrorAction Stop
    }
}
if (-not (Test-Path -LiteralPath $python)) {
    & $bootstrapPython -I -m venv $venv
    if ($LASTEXITCODE -ne 0) { throw 'Paketleme sanal ortamı oluşturulamadı.' }
}

$runtimeLayoutJson = & $python -I -c 'import ctypes,json,sys; b=ctypes.create_unicode_buffer(32768); n=ctypes.windll.kernel32.GetSystemDirectoryW(b,len(b)); assert 0<n<len(b); print(json.dumps({"basePrefix":sys.base_prefix,"systemDirectory":b.value}))'
if ($LASTEXITCODE -ne 0) { throw 'Python ve Windows çalışma zamanı dizinleri belirlenemedi.' }
$runtimeLayout = $runtimeLayoutJson | ConvertFrom-Json
$buildPath = @(
    (Join-Path $venv 'Scripts'),
    $runtimeLayout.basePrefix,
    (Join-Path $runtimeLayout.basePrefix 'DLLs'),
    $runtimeLayout.systemDirectory,
    (Split-Path $runtimeLayout.systemDirectory -Parent)
) | Where-Object { Test-Path -LiteralPath $_ -PathType Container } | Select-Object -Unique
$env:PATH = $buildPath -join [IO.Path]::PathSeparator

& $python -I -m pip install --disable-pip-version-check --index-url https://pypi.org/simple --only-binary=:all: --force-reinstall --require-hashes -r $dependencyLock
if ($LASTEXITCODE -ne 0) { throw 'Kilitli paketleme bağımlılıkları kurulamadı.' }
& $python -I -m pip check
if ($LASTEXITCODE -ne 0) { throw 'Paketleme bağımlılık denetimi başarısız.' }

$tkinterCheck = @'
import os
from pathlib import Path
from PyInstaller.utils.hooks.tcl_tk import tcltk_info
names = ("PYTHONPATH", "PYTHONHOME", "TCL_LIBRARY", "TK_LIBRARY")
remaining = [name for name in names if name in os.environ]
if remaining:
    raise SystemExit(f"Build environment variables were not removed: {remaining}")
if not tcltk_info.available or tcltk_info.tcl_data_missing or tcltk_info.tk_data_missing:
    raise SystemExit("PyInstaller isolated tkinter/Tcl/Tk preflight failed")
if not all(Path(path).is_dir() for path in (tcltk_info.tcl_data_dir, tcltk_info.tk_data_dir)):
    raise SystemExit("Python Tcl/Tk data directories are missing")
print(f"PyInstaller tkinter preflight verified: {tcltk_info.tkinter_extension_file}")
'@
& $python -I -c $tkinterCheck
if ($LASTEXITCODE -ne 0) { throw 'Paketleme tkinter/Tcl/Tk ön denetimi başarısız.' }

$lockCheck = @'
import re
import sys
from importlib.metadata import distributions
from pathlib import Path

def canonical(name):
    return re.sub(r"[-_.]+", "-", name).lower()

expected = {}
for number, raw in enumerate(Path(sys.argv[1]).read_text(encoding="utf-8-sig").splitlines(), 1):
    line = raw.strip()
    if not line or line.startswith("#"):
        continue
    match = re.fullmatch(r"([A-Za-z0-9][A-Za-z0-9._-]*)==([^\s]+)(?:\s+--hash=sha256:[0-9a-fA-F]{64})+", line)
    if not match:
        raise SystemExit(f"Invalid dependency lock entry at line {number}")
    name = canonical(match.group(1))
    if name in expected:
        raise SystemExit(f"Duplicate dependency lock entry: {name}")
    expected[name] = match.group(2)
if not expected:
    raise SystemExit("Dependency lock must not be empty")

installed = {}
for distribution in distributions():
    name = canonical(distribution.metadata["Name"])
    if name == "pip":
        continue
    if name in installed:
        raise SystemExit(f"Duplicate installed distribution: {name}")
    installed[name] = distribution.version

problems = []
for name in sorted(installed.keys() - expected.keys()):
    problems.append(f"undeclared package: {name}=={installed[name]}")
for name in sorted(expected.keys() - installed.keys()):
    problems.append(f"missing package: {name}=={expected[name]}")
for name in sorted(expected.keys() & installed.keys()):
    if installed[name] != expected[name]:
        problems.append(f"version mismatch: {name} expected {expected[name]}, found {installed[name]}")
if problems:
    raise SystemExit("Build environment differs from dependency lock:\n" + "\n".join(problems))
print(f"Locked build environment verified: {len(expected)} packages")
'@
& $python -I -c $lockCheck $dependencyLock
if ($LASTEXITCODE -ne 0) { throw 'Paketleme ortamı bağımlılık kilidiyle eşleşmiyor.' }

$requestsCheck = @'
from importlib.metadata import version
from packaging.version import Version
installed = version("requests")
if Version(installed) < Version("2.34.2"):
    raise SystemExit(f"Requests >=2.34.2 gerekli; bulunan: {installed}")
print(f"Requests security baseline: {installed}")
'@
& $python -I -c $requestsCheck
if ($LASTEXITCODE -ne 0) { throw 'Requests güvenlik sürümü denetimi başarısız.' }

$common = @(
    '--noconfirm', '--clean', '--windowed', '--noupx',
    '--paths', $parent,
    '--icon', (Join-Path $project 'assets\blackwood_mark_generated.ico'),
    '--version-file', (Join-Path $PSScriptRoot 'version_info.txt'),
    '--add-data', "$(Join-Path $project 'assets');ui_mirror/assets",
    '--exclude-module', 'playwright',
    '--distpath', $dist,
    '--workpath', (Join-Path $build 'work'),
    '--specpath', (Join-Path $build 'spec')
)

Push-Location $parent
try {
    & $python -I -m PyInstaller @common --onedir --name 'Blackwood UI Mirror' (Join-Path $project 'desktop.py')
    if ($LASTEXITCODE -ne 0) { throw 'Uygulama EXE derlemesi başarısız.' }

    $nativeVerifier = Join-Path $PSScriptRoot 'verify_native_inputs.py'
    $onedirNativeReport = Join-Path $dist 'native-inputs-onedir.json'
    & $python -I $nativeVerifier --toc (Join-Path $build 'work\Blackwood UI Mirror\Analysis-00.toc') --output $onedirNativeReport
    if ($LASTEXITCODE -ne 0) { throw 'Uygulama yerel bağımlılık köken denetimi başarısız.' }

    $payloadManifest = Join-Path $build 'installer-payload.nsh'
    & $python -I (Join-Path $PSScriptRoot 'generate_installer_manifest.py') --build-dir (Join-Path $dist 'Blackwood UI Mirror') --output $payloadManifest
    if ($LASTEXITCODE -ne 0) { throw 'Güvenli kaldırma dosya listesi oluşturulamadı.' }

    if (-not $SkipPortable) {
        & $python -I -m PyInstaller @common --onefile --name 'Blackwood-UIMirror-Portable' (Join-Path $project 'desktop.py')
        if ($LASTEXITCODE -ne 0) { throw 'Taşınabilir EXE derlemesi başarısız.' }
        $portableNativeReport = Join-Path $dist 'native-inputs-portable.json'
        & $python -I $nativeVerifier --toc (Join-Path $build 'work\Blackwood-UIMirror-Portable\Analysis-00.toc') --output $portableNativeReport
        if ($LASTEXITCODE -ne 0) { throw 'Taşınabilir yerel bağımlılık köken denetimi başarısız.' }
    }

    & $nsis "/DPROJECT_DIR=$project" "/DBUILD_DIR=$(Join-Path $dist 'Blackwood UI Mirror')" "/DOUTPUT_DIR=$dist" "/DPAYLOAD_MANIFEST=$payloadManifest" (Join-Path $PSScriptRoot 'installer.nsi')
    if ($LASTEXITCODE -ne 0) { throw 'Kurulum EXE derlemesi başarısız.' }
}
finally {
    Pop-Location
}

$runtimeScript = @'
import json
import platform
import sys
from importlib.metadata import distributions
print(json.dumps({
    "version": sys.version,
    "implementation": platform.python_implementation(),
    "architecture": platform.machine(),
    "packages": dict(sorted((d.metadata["Name"], d.version) for d in distributions())),
}))
'@
$runtimeJson = & $python -I -c $runtimeScript
if ($LASTEXITCODE -ne 0) { throw 'Derleme ortamı kaydı alınamadı.' }
$runtimeInfo = $runtimeJson | ConvertFrom-Json
$artifactPaths = @(
    (Join-Path $dist 'Blackwood UI Mirror\Blackwood UI Mirror.exe'),
    (Join-Path $dist 'Blackwood-UIMirror-Setup.exe')
)
if (-not $SkipPortable) {
    $artifactPaths += Join-Path $dist 'Blackwood-UIMirror-Portable.exe'
}
$artifacts = foreach ($artifactPath in $artifactPaths) {
    $artifact = Get-Item -LiteralPath $artifactPath
    [ordered]@{
        path = $artifact.FullName
        bytes = $artifact.Length
        sha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $artifact.FullName).Hash
    }
}
$manifest = [ordered]@{
    builtAtUtc = (Get-Date).ToUniversalTime().ToString('o')
    pythonExecutable = $python
    runtime = $runtimeInfo
    dependencyLockSha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $dependencyLock).Hash
    portableBuilt = -not [bool]$SkipPortable
    outputs = @($artifacts)
    buildSearchPath = @($buildPath)
    nativeInputs = [ordered]@{
        onedir = (Get-Content -LiteralPath $onedirNativeReport -Raw | ConvertFrom-Json)
    }
}
if (-not $SkipPortable) {
    $manifest.nativeInputs['portable'] = Get-Content -LiteralPath $portableNativeReport -Raw | ConvertFrom-Json
}
$manifest | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath (Join-Path $dist 'build-manifest.json') -Encoding UTF8

Get-Item -LiteralPath $artifactPaths |
    Select-Object FullName, Length
}
finally {
    foreach ($name in $environmentNames) {
        if ($null -eq $previousEnvironment[$name]) {
            if (Test-Path -LiteralPath "Env:$name") {
                Remove-Item -LiteralPath "Env:$name" -ErrorAction Stop
            }
        }
        else {
            [Environment]::SetEnvironmentVariable($name, $previousEnvironment[$name], 'Process')
        }
    }
}
