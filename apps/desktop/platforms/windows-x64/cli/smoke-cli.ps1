# Run the bundled agent CLI (deeptutor.cmd) from the packaged app folder with
# a fresh, non-ASCII user path and no developer tools on PATH. Exercises the
# cold-start path: the CLI itself extracts the sealed runtime on first use.
$ErrorActionPreference = 'Stop'
# cli/ sits one level below the other platform scripts: ../../.. is apps/desktop.
$desktop = (Resolve-Path "$PSScriptRoot/../../..").Path
# @electron/packager writes the app to out/<name>-win32-<arch>.
$appRoot = Get-ChildItem "$desktop/out" -Directory -Filter '*-win32-x64' |
    Sort-Object LastWriteTime -Descending | Select-Object -First 1
if (!$appRoot) { throw "Expected the packaged app folder (*-win32-x64) under $desktop\out from npm run package" }
$cmd = Join-Path $appRoot.FullName 'deeptutor.cmd'
if (!(Test-Path $cmd)) { throw "deeptutor.cmd is missing from the packaged app: $cmd" }

$root = Join-Path $env:TEMP "DeepTutor CLI smoke $([guid]::NewGuid())"
$userRoot = Join-Path $root 'user data'
New-Item -ItemType Directory -Path $userRoot -Force | Out-Null
$log = Join-Path $desktop '.local/windows-x64/cli-smoke.log'
$oldPath = $env:PATH
$oldHome = $env:OPEN_DEEPTUTOR_TEST_HOME
try {
    # Same isolation as smoke-installer.ps1: only OS tools on PATH, so the test
    # proves the bundled runtime (not a developer Python) answers.
    $env:OPEN_DEEPTUTOR_TEST_HOME = $userRoot
    $env:PATH = "$env:SystemRoot\System32;$env:SystemRoot;$env:SystemRoot\System32\Wbem"

    Write-Host 'Cold-starting the bundled CLI (first use extracts the sealed runtime)...'
    & cmd /c "`"$cmd`" --help" 2>&1 | Tee-Object -FilePath $log
    if ($LASTEXITCODE -ne 0) { throw "deeptutor --help exited $LASTEXITCODE" }

    # Cold start must have installed the runtime under the user root the
    # desktop app also uses, with the installed.json marker installBundle reads.
    $runtime = Get-ChildItem (Join-Path $userRoot 'runtimes') -Directory |
        Where-Object { Test-Path (Join-Path $_.FullName 'installed.json') } |
        Select-Object -First 1
    if (!$runtime) { throw 'CLI cold start did not install the sealed runtime with installed.json' }
    $python = Join-Path $runtime.FullName 'python\python.exe'
    if (!(Test-Path $python)) { throw "Runtime is missing the bundled Python: $python" }

    # The runtime the CLI installed must be exactly the one the shell ships.
    $bundled = Get-Content (Join-Path $appRoot.FullName 'resources\seed\runtime.json') -Raw | ConvertFrom-Json
    if ($runtime.Name -ne $bundled.runtimeId) { throw "CLI installed runtime $($runtime.Name), the app ships $($bundled.runtimeId)" }

    & cmd /c "`"$cmd`" --help" *> $null   # warm start: reuse without re-extracting
    if ($LASTEXITCODE -ne 0) { throw "warm deeptutor --help exited $LASTEXITCODE" }

    $modules = & cmd /c "`"$cmd`" module list" 2>&1 | Tee-Object -FilePath $log -Append
    if ($LASTEXITCODE -ne 0) { throw "deeptutor module list exited $LASTEXITCODE" }

    $config = & cmd /c "`"$cmd`" config show" 2>&1 | Tee-Object -FilePath $log -Append
    if ($LASTEXITCODE -ne 0) { throw "deeptutor config show exited $LASTEXITCODE" }
    if (($config -join "`n") -notmatch '"ports"') {
        throw "deeptutor config show did not print its JSON report: $($config -join ' ')"
    }

    Write-Host 'PASS: the packaged app carries a working bundled CLI (cold start, warm start, module list, config show).'
} finally {
    $env:PATH = $oldPath
    $env:OPEN_DEEPTUTOR_TEST_HOME = $oldHome
    # The extracted runtime contains site-package paths beyond MAX_PATH; the
    # \\?\ prefix lets PowerShell 5.1 delete them. Cleanup is best-effort:
    # a temp folder left behind must not fail an otherwise passing run.
    try { Remove-Item -LiteralPath "\\?\$root" -Recurse -Force -ErrorAction Stop }
    catch { Write-Warning "Could not clean up temp folder $root : $_" }
    # $log already archives the transcript in place via Tee-Object.
}
