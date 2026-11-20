# Run the repo's official CLI unit tests (tests/cli) against the sealed runtime
# the bundled agent CLI (deeptutor.cmd) uses. One command, no repo venv needed:
#
#   powershell -NoProfile -ExecutionPolicy Bypass -File test-cli-units.ps1
#
# What it does:
#   1. Locates the DeepTutor checkout (tests\cli) by walking up from this script.
#   2. Resolves the sealed runtime via the packaged app's deeptutor-ensure.ps1 —
#      the same path deeptutor.cmd takes — cold-starting it on first use, so the
#      tests exercise exactly the runtime the shipped CLI will run on.
#   3. Installs pytest + pytest-asyncio into an isolated --target dir (the sealed
#      runtime itself is never modified; its pip is PEP 668 protected anyway).
#   4. Runs the full tests/cli suite with the runtime's python.exe, an isolated
#      DEEPTUTOR_HOME, and the same interpreter flags deeptutor.cmd sets
#      (-P, PYTHONUTF8, PYTHONDONTWRITEBYTECODE) so no .pyc pollutes the core.
#
# Options:
#   -Runtime <dir>   Test this runtime dir instead of resolving via the app
#                    (e.g. "%LOCALAPPDATA%\Open DeepTutor\runtimes\<runtimeId>").
#   -PytestArgs ...  Extra pytest arguments, e.g. -PytestArgs "-k","doctor"
#   -Tests <dir>     Tests dir override (default: <repo>\tests\cli).
#
# Known issue (not packaging related): as of 1.7.3, two notebook tests
# (test_notebook_add_md_creates_record / test_notebook_replace_md_updates_record)
# fail on Windows from a source-level CRLF bug in deeptutor_cli/notebook.py —
# they fail the same way on any native Windows install.
#
# Also: the four environment-lease tests in test_runtime_cleanup.py hold an
# exclusive lock keyed on sys.prefix. Any other process running from the same
# sealed runtime (the desktop app backend, a deeptutor serve, even a script
# started with that python.exe) makes them fail with "Environment is in use.
# Stop DeepTutor before changing modules." Stop other users of the runtime
# first; the failure is a lock conflict, not a runtime defect.

param(
    [string]$Runtime = "",
    [string]$Tests = "",
    [string[]]$PytestArgs = @()
)

$ErrorActionPreference = 'Stop'

# --- 1. Locate the DeepTutor checkout containing tests\cli -------------------
# Works from both the source platforms\windows-x64\cli\ copy and the packaged
# app copy under apps\desktop\out\<name>-win32-x64\cli\: both sit inside the repo.
$repo = $PSScriptRoot
while ($repo -and !(Test-Path (Join-Path $repo 'tests\cli'))) {
    $parent = Split-Path $repo -Parent
    if ($parent -eq $repo) { $repo = $null; break }
    $repo = $parent
}
if (!$repo) { throw "Could not locate the DeepTutor checkout (tests\cli) above $PSScriptRoot" }

$testsDir = if ($Tests) { (Resolve-Path $Tests).Path } else { Join-Path $repo 'tests\cli' }
if (!(Test-Path $testsDir)) { throw "tests\cli not found: $testsDir" }

# --- 2. Resolve the sealed runtime --------------------------------------------
if ($Runtime) {
    $runtimeRoot = (Resolve-Path $Runtime).Path
} else {
    # apps/desktop from either location (cli -> windows-x64|<app> -> platforms|out -> desktop).
    $desktop = (Resolve-Path "$PSScriptRoot/../../..").Path

    # If this copy sits inside the packaged app, its own cli\deeptutor-ensure.ps1
    # can resolve the seed directly. Otherwise find the packaged app under out\,
    # like smoke-cli.ps1 does.
    if (Test-Path (Join-Path $PSScriptRoot '..\resources\seed')) {
        $appRoot = (Resolve-Path "$PSScriptRoot/..").Path
    } else {
        $appRoot = Get-ChildItem "$desktop/out" -Directory -Filter '*-win32-x64' |
            Sort-Object LastWriteTime -Descending | Select-Object -First 1
        if (!$appRoot) {
            throw "Expected the packaged app folder (*-win32-x64) under $desktop\out from npm run package"
        }
        $appRoot = $appRoot.FullName
    }
    $ensure = Join-Path $appRoot 'cli\deeptutor-ensure.ps1'
    if (!(Test-Path $ensure)) { $ensure = Join-Path $appRoot 'deeptutor-ensure.ps1' }
    if (!(Test-Path $ensure)) { throw "deeptutor-ensure.ps1 is missing from the packaged app: $ensure" }

    Write-Host "Resolving the sealed runtime via the packaged CLI (cold-starts on first use)..."
    # deeptutor-ensure.ps1 prints the runtime root, or throws with a message.
    $runtimeRoot = (& $ensure | Select-Object -First 1)
    if (!$runtimeRoot) { throw "deeptutor-ensure.ps1 did not print a runtime root" }
}

$python = Join-Path $runtimeRoot 'python\python.exe'
if (!(Test-Path $python)) { throw "Sealed runtime is missing the bundled Python: $python" }
Write-Host "Runtime: $runtimeRoot"

# --- 3. Isolated pytest install (cached under .local) --------------------------
# $desktop is only resolved in the default branch above; with -Runtime fall back
# to the repo root for the cache.
$cacheRoot = if ($desktop) { $desktop } else { $repo }
$cache = Join-Path $cacheRoot '.local/windows-x64'
$wheelhouse = Join-Path $cache 'cli-test-wheelhouse'
$deps = Join-Path $cache 'cli-test-deps'

if (!(Test-Path (Join-Path $wheelhouse 'pytest-*.whl'))) {
    New-Item -ItemType Directory -Path $wheelhouse -Force | Out-Null
    Write-Host 'Downloading pytest wheels with the sealed runtime (one-time)...'
    & $python -m pip download pytest pytest-asyncio -d $wheelhouse --quiet
    if ($LASTEXITCODE -ne 0) { throw "pip download failed (is the network reachable?)" }
}
if (!(Test-Path (Join-Path $deps 'pytest'))) {
    New-Item -ItemType Directory -Path $deps -Force | Out-Null
    Write-Host 'Installing pytest into the isolated test-deps dir...'
    & $python -m pip install --no-index --find-links $wheelhouse --target $deps pytest pytest-asyncio --quiet
    if ($LASTEXITCODE -ne 0) { throw "pip install into $deps failed" }
}

# --- 4. Run the official suite with the sealed interpreter --------------------
# NB: not named $home — that is a read-only automatic variable in PowerShell.
$testHome = Join-Path $env:TEMP "DeepTutor CLI unit-test home $([guid]::NewGuid())"
New-Item -ItemType Directory -Path $testHome -Force | Out-Null
$log = Join-Path $cache 'cli-unit-tests.log'

$oldPythonPath = $env:PYTHONPATH
$oldUtf8 = $env:PYTHONUTF8
$oldIoEncoding = $env:PYTHONIOENCODING
$oldNoBytecode = $env:PYTHONDONTWRITEBYTECODE
$oldDtHome = $env:DEEPTUTOR_HOME
try {
    # Same interpreter environment deeptutor.cmd sets before -m deeptutor_cli.main.
    $env:PYTHONPATH = "$runtimeRoot\core;$deps"
    $env:PYTHONUTF8 = '1'
    $env:PYTHONIOENCODING = 'utf-8'
    $env:PYTHONDONTWRITEBYTECODE = '1'
    $env:DEEPTUTOR_HOME = $testHome

    Write-Host "Running tests\cli against the sealed runtime (isolated DEEPTUTOR_HOME)..."
    Push-Location $repo
    try {
        # -P keeps the caller's directory out of sys.path, like the shipped CLI.
        & $python -P -m pytest $testsDir -q -p no:cacheprovider @PytestArgs 2>&1 |
            Tee-Object -FilePath $log
        $code = $LASTEXITCODE
    } finally {
        Pop-Location
    }
} finally {
    $env:PYTHONPATH = $oldPythonPath
    $env:PYTHONUTF8 = $oldUtf8
    $env:PYTHONIOENCODING = $oldIoEncoding
    $env:PYTHONDONTWRITEBYTECODE = $oldNoBytecode
    $env:DEEPTUTOR_HOME = $oldDtHome
    # The isolated home may hold site-package paths beyond MAX_PATH; the \\?\
    # prefix lets PowerShell 5.1 delete them. Best-effort, like smoke-cli.ps1.
    try { Remove-Item -LiteralPath "\\?\$testHome" -Recurse -Force -ErrorAction Stop }
    catch { Write-Warning "Could not clean up temp folder $testHome : $_" }
}

if ($code -eq 0) {
    Write-Host 'PASS: the sealed runtime passes the official CLI unit tests.'
} else {
    Write-Host "pytest exited $code (see $log)."
}
exit $code
