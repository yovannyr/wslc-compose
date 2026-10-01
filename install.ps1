# Windows installer. No administrator privileges or global PATH changes required.
[CmdletBinding()]
param(
    [string]$Source = $PSScriptRoot,
    [string]$Python = 'python',
    # Packaged hosts can virtualize LOCALAPPDATA. Use a user-home directory
    # so Python launchers and profile paths work outside the installing app.
    [string]$InstallDirectory = (Join-Path $env:USERPROFILE '.wslc-compose\venv'),
    [string]$ProfilePath = $PROFILE.CurrentUserAllHosts,
    [switch]$SkipPackageInstall,
    [string]$WrapperPath,
    [switch]$EnableCmd,
    [string]$CmdRegistryPath = 'HKCU:\Software\Microsoft\Command Processor',
    [switch]$Uninstall
)

$ErrorActionPreference = 'Stop'
if ($EnableCmd) {
    . (Join-Path $PSScriptRoot 'scripts\cmd-integration.ps1')
}
$startMarker = '# >>> wslc-compose >>>'
$endMarker = '# <<< wslc-compose <<<'
$blockPattern = '(?ms)\r?\n# >>> wslc-compose >>>\r?\n.*?^# <<< wslc-compose <<<\r?\n'
$ProfilePath = [IO.Path]::GetFullPath($ProfilePath)
$profileText = ''
$profileEncoding = New-Object Text.UTF8Encoding($true)
if (Test-Path -LiteralPath $ProfilePath) {
    $reader = New-Object IO.StreamReader($ProfilePath, $profileEncoding, $true)
    try {
        $profileText = $reader.ReadToEnd()
        $profileEncoding = $reader.CurrentEncoding
    } finally { $reader.Dispose() }
}
$blocks = [regex]::Matches($profileText, $blockPattern)
if (($profileText.Contains($startMarker) -or $profileText.Contains($endMarker)) -and
    ($blocks.Count -ne 1)) {
    throw "Incomplete or duplicate wslc-compose markers in $ProfilePath. Profile left unchanged."
}
$unmanagedText = [regex]::Replace($profileText, $blockPattern, '')
if (!$Uninstall -and $unmanagedText -match
    '(?im)^\s*(?:function\s+(?:global:)?wslc(?:-compose)?\b|(?:Set|New)-Alias\s+.*\bwslc\b)') {
    throw "An existing wslc function or alias was found in $ProfilePath. Profile left unchanged."
}

if ($Uninstall) {
    if ($EnableCmd) {
        Update-WslcCmdIntegration -RegistryPath $CmdRegistryPath -Remove
    }
    $updatedText = $unmanagedText
} else {
    if (!$SkipPackageInstall) {
        if (!$env:OS -or $env:OS -ne 'Windows_NT') { throw 'This installer requires Windows.' }
        $InstallDirectory = [IO.Path]::GetFullPath($InstallDirectory)
        $venvPython = Join-Path $InstallDirectory 'Scripts\python.exe'
        if (!(Test-Path -LiteralPath $venvPython)) {
            & $Python -m venv $InstallDirectory
            if ($LASTEXITCODE -ne 0) { throw 'Could not create the Python virtual environment.' }
        }
        & $venvPython -m pip install --upgrade --force-reinstall --no-cache-dir $Source
        if ($LASTEXITCODE -ne 0) { throw 'Package installation failed. Profile left unchanged.' }
        $WrapperPath = Join-Path $InstallDirectory 'Scripts\wslc.exe'
    } elseif (!$WrapperPath) {
        throw '-SkipPackageInstall requires -WrapperPath (the installed wrapper, not Microsoft wslc).'
    }
    $WrapperPath = (Resolve-Path -LiteralPath $WrapperPath).ProviderPath
    $composePath = Join-Path (Split-Path -Parent $WrapperPath) 'wslc-compose.exe'
    if (!(Test-Path -LiteralPath $composePath)) { throw "Missing compose executable: $composePath" }
    & $composePath version
    if ($LASTEXITCODE -ne 0) { throw 'Installed compose executable failed verification.' }
    $quotedWrapper = $WrapperPath.Replace("'", "''")
    $quotedCompose = $composePath.Replace("'", "''")
    # Invoke the existing wrapper rather than duplicating dispatch/backend logic.
    $block = @"
$startMarker
function global:wslc { & '$quotedWrapper' @args }
function global:wslc-compose { & '$quotedCompose' @args }
$endMarker
"@
    $updatedText = $unmanagedText + "`r`n" + $block.Replace("`r`n", "`n").Replace("`n", "`r`n") + "`r`n"
    $tokens = $null
    $parseErrors = $null
    [void][Management.Automation.Language.Parser]::ParseInput($updatedText, [ref]$tokens, [ref]$parseErrors)
    if ($parseErrors.Count) { throw 'Profile has syntax errors. Profile left unchanged.' }
    if ($EnableCmd) {
        Update-WslcCmdIntegration -RegistryPath $CmdRegistryPath -WrapperDirectory (Split-Path -Parent $WrapperPath)
    }
}
if ($updatedText -ne $profileText) {
    if (Test-Path -LiteralPath $ProfilePath) {
        $backupPath = $ProfilePath + '.wslc-compose-backup-' + [guid]::NewGuid().ToString('N')
        Copy-Item -LiteralPath $ProfilePath -Destination $backupPath
        Write-Host "Profile backup: $backupPath"
    } else {
        [void][IO.Directory]::CreateDirectory((Split-Path -Parent $ProfilePath))
    }
    [IO.File]::WriteAllText($ProfilePath, $updatedText, $profileEncoding)
}
if ($Uninstall) {
    Write-Host 'PowerShell integration removed. The package and backups were retained.'
} else {
    Write-Host "Installed PowerShell integration in $ProfilePath"
    Write-Host 'Open a new PowerShell terminal, or reload this profile:'
    Write-Host ". '$($ProfilePath.Replace("'", "''"))'"
    Write-Host 'Try: wslc compose version; wslc list'
}
if ($EnableCmd) {
    if ($Uninstall) { Write-Host 'CMD integration removed.' }
    else { Write-Host 'CMD integration installed. Open a new cmd.exe window (without /d).' }
}
