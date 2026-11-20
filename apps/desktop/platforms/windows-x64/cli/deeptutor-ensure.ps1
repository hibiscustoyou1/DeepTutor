# Finds or installs the sealed DeepTutor core runtime for the bundled CLI.
# Mirrors the desktop shell's installBundle() (apps/desktop/src/bundles.mjs):
# same destination, same tar extraction, same installed.json marker so the
# desktop app reuses (rather than duplicates) a runtime installed by the CLI.
#
# Prints the runtime root on success. Throws on failure.
$ErrorActionPreference = 'Stop'

# The launchers sit either in the app root or in its cli\ subfolder (package.mjs
# copies them to both). $seedRoot walks up to whichever holds the seed.
$seedRoot = $PSScriptRoot
while ($seedRoot -and !(Test-Path (Join-Path $seedRoot 'resources\seed'))) {
    $parent = Split-Path $seedRoot -Parent
    if ($parent -eq $seedRoot) { $seedRoot = $null; break }
    $seedRoot = $parent
}
if (!$seedRoot) { throw "No resources\seed folder above $PSScriptRoot; is this inside the installed app?" }
$manifestFile = Join-Path $seedRoot 'resources\seed\runtime.json'
if (!(Test-Path $manifestFile)) { throw "No bundled runtime manifest at $manifestFile" }
$manifest = Get-Content $manifestFile -Raw | ConvertFrom-Json
if (!$manifest.runtimeId) { throw 'The bundled runtime manifest is invalid' }

# Same user root as the desktop app (src/main.mjs), unless tests override it.
$root = if ($env:OPEN_DEEPTUTOR_TEST_HOME) { $env:OPEN_DEEPTUTOR_TEST_HOME }
        else { Join-Path $env:LOCALAPPDATA 'Open DeepTutor' }
$destination = Join-Path $root "runtimes\$($manifest.runtimeId)"
$python = Join-Path $destination 'python\python.exe'
if (Test-Path $python) {
    Write-Output $destination
    exit 0
}

# Fresh install: verify the archive checksum like installBundle() does.
$archive = Join-Path $seedRoot "resources\seed\$($manifest.archive)"
if (!(Test-Path $archive)) { throw "Bundled runtime archive is missing: $archive" }
$actual = (Get-FileHash $archive -Algorithm SHA256).Hash.ToLower()
if ($actual -ne $manifest.sha256) {
    throw "Bundled runtime checksum mismatch: expected $($manifest.sha256), got $actual"
}

$staging = "$destination.installing"
if (Test-Path $staging) { Remove-Item $staging -Recurse -Force }
New-Item -ItemType Directory -Path $staging -Force | Out-Null
try {
    # Windows 10+ ships bsdtar as tar.exe; it handles tar.gz natively.
    & "$env:SystemRoot\System32\tar.exe" -xzf $archive -C $staging
    if ($LASTEXITCODE -ne 0) { throw "Runtime extraction exited $LASTEXITCODE" }

    # The layout markers installBundle() checks before accepting a runtime.
    foreach ($marker in @('python\python.exe', 'node\node.exe',
            'core\deeptutor_cli\main.py', 'web\server.js')) {
        if (!(Test-Path (Join-Path $staging $marker))) { throw "Extracted runtime is missing $marker" }
    }

    # installed.json is what lets the desktop app's installBundle() reuse this
    # runtime instead of extracting the seed a second time.
    Copy-Item $manifestFile (Join-Path $staging 'installed.json') -Force
    if (Test-Path $destination) { Remove-Item $destination -Recurse -Force }
    New-Item -ItemType Directory -Path (Split-Path $destination -Parent) -Force | Out-Null
    Move-Item $staging $destination

    Write-Output $destination
} catch {
    if (Test-Path $staging) { Remove-Item $staging -Recurse -Force }
    throw
}
