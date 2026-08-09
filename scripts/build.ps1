param(
    [switch]$ReuseEnvironment,
    [switch]$BuildInstaller
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

$Venv = Join-Path $ProjectRoot ".build-venv"
$Python = Join-Path $Venv "Scripts\python.exe"

function Assert-NativeSuccess([string]$Step) {
    if ($LASTEXITCODE -ne 0) {
        throw "$Step failed with exit code $LASTEXITCODE."
    }
}

if (-not $ReuseEnvironment -or -not (Test-Path $Python)) {
    if (Get-Command uv -ErrorAction SilentlyContinue) {
        uv venv --python 3.11 --clear --seed $Venv
        Assert-NativeSuccess "Creating the Python environment"
    } else {
        $PythonLauncher = Get-Command py -ErrorAction SilentlyContinue
        if (-not $PythonLauncher) {
            throw "Python 3.11 or uv is required to build this project."
        }
        & $PythonLauncher.Source -3.11 -m venv --clear $Venv
        Assert-NativeSuccess "Creating the Python environment"
    }
    & $Python -m pip install --upgrade pip
    Assert-NativeSuccess "Upgrading pip"
    & $Python -m pip install -r requirements-build.txt
    Assert-NativeSuccess "Installing build requirements"
    & $Python -m pip install --no-deps rapidocr==3.9.2
    Assert-NativeSuccess "Installing RapidOCR with the headless OpenCV runtime"
    & $Python -m pip install --no-deps -e .
    Assert-NativeSuccess "Installing the application"
}

& $Python scripts\create_icon.py
Assert-NativeSuccess "Creating the application icon"
& $Python scripts\create_smoke_fixture.py --output build\smoke_owner.jpg
Assert-NativeSuccess "Creating the OCR smoke fixture"
& $Python -m pytest -q --basetemp build\pytest-build
Assert-NativeSuccess "Running tests"
& $Python -m PyInstaller --noconfirm --clean ConstructionOwnerClassifier.spec
Assert-NativeSuccess "Building the application payload"

$Stage = Join-Path $ProjectRoot "dist\ConstructionOwnerClassifier"
$Exe = Join-Path $Stage "ConstructionOwnerClassifier.exe"
if (-not (Test-Path $Exe)) {
    throw "Build completed without the expected executable: $Exe"
}

$Sample = Get-Item (Join-Path $ProjectRoot "build\smoke_owner.jpg")
Write-Host "Packaged OCR sample: $($Sample.FullName)"
$Smoke = Start-Process -FilePath $Exe -ArgumentList "--smoke-ocr", "`"$($Sample.FullName)`"" -Wait -PassThru -WindowStyle Hidden
if ($Smoke.ExitCode -ne 0) {
    throw "Packaged OCR smoke test failed with exit code $($Smoke.ExitCode)."
}

$InstalledMB = [math]::Round(((Get-ChildItem $Stage -Recurse -File | Measure-Object Length -Sum).Sum) / 1MB, 1)
if ($InstalledMB -gt 340) {
    throw "Installed payload is $InstalledMB MB, above the 340 MB limit."
}

if (-not $BuildInstaller) {
    Write-Host "Portable test build: $Stage ($InstalledMB MB)"
    return
}

$InnoCandidates = @(
    "C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
    "C:\Program Files\Inno Setup 6\ISCC.exe",
    "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe"
)
$Iscc = $InnoCandidates | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $Iscc) {
    $Iscc = Get-ChildItem "$env:LOCALAPPDATA\Microsoft\WinGet\Packages" -Recurse -Filter ISCC.exe -ErrorAction SilentlyContinue |
        Select-Object -First 1 -ExpandProperty FullName
}
if (-not $Iscc) {
    throw "Inno Setup 6 is required. Install it with: winget install JRSoftware.InnoSetup"
}

& $Iscc installer\ConstructionOwnerClassifier.iss
Assert-NativeSuccess "Compiling the installer"
$Installer = Join-Path $ProjectRoot "dist\施工责任人图片分类器-Setup-1.1.2.exe"
if (-not (Test-Path $Installer)) {
    throw "Inno Setup did not create the expected installer."
}
$InstallerMB = [math]::Round((Get-Item $Installer).Length / 1MB, 1)
if ($InstallerMB -gt 130) {
    throw "Installer is $InstallerMB MB, above the 130 MB target."
}

Write-Host "Installed payload: $InstalledMB MB"
Write-Host "Installer: $Installer ($InstallerMB MB)"
