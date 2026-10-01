# Keep CMD AutoRun changes separate so tests can use a disposable registry key.
function Update-WslcCmdIntegration {
    param(
        [string]$RegistryPath,
        [string]$WrapperDirectory,
        [switch]$Remove
    )
    $stateName = 'WslcComposeIntegration'
    $key = Get-Item -LiteralPath $RegistryPath -ErrorAction SilentlyContinue
    $current = $null
    $state = $null
    $kind = 'String'
    if ($key) {
        $current = $key.GetValue('AutoRun', $null,
            [Microsoft.Win32.RegistryValueOptions]::DoNotExpandEnvironmentNames)
        if ($null -ne $current) {
            $kind = $key.GetValueKind('AutoRun').ToString()
            if ($kind -notin @('String', 'ExpandString')) {
                throw 'CMD AutoRun is not a string. Registry left unchanged.'
            }
        }
        $saved = $key.GetValue($stateName)
        if ($saved) { $state = $saved | ConvertFrom-Json }
    }
    $base = $current
    if ($state) {
        if ($state.Version -ne 1 -or !$state.Suffix -or $null -eq $current -or
            !$current.EndsWith($state.Suffix, [StringComparison]::Ordinal)) {
            throw 'CMD AutoRun was changed externally. Registry left unchanged; inspect the saved WslcComposeIntegration value.'
        }
        $base = $current.Substring(0, $current.Length - $state.Suffix.Length)
    }
    if ($Remove) {
        if (!$state) { return }
        if (!$base -and !$state.HadAutoRun) {
            Remove-ItemProperty -LiteralPath $RegistryPath -Name AutoRun
        } else {
            New-ItemProperty -LiteralPath $RegistryPath -Name AutoRun -Value ([string]$base) -PropertyType $kind -Force | Out-Null
        }
        Remove-ItemProperty -LiteralPath $RegistryPath -Name $stateName
        return
    }
    # Percent expansion and delayed expansion would corrupt such directories.
    if ($WrapperDirectory -match '[%!"\r\n]') {
        throw 'CMD integration cannot use a wrapper directory containing %, !, quotes or newlines.'
    }
    $command = 'set "PATH=' + $WrapperDirectory + ';%PATH%"'
    $suffix = if ([string]::IsNullOrEmpty($base)) { $command } else { ' & ' + $command }
    $updated = [string]$base + $suffix
    if ($state -and $updated -ceq $current) { return }
    if (!$key) { New-Item -Path $RegistryPath -Force | Out-Null }
    $hadAutoRun = if ($state) { $state.HadAutoRun } else { $null -ne $current }
    $original = if ($state) { $state.Original } else { $current }
    $newState = @{
        Version = 1; Suffix = $suffix; HadAutoRun = $hadAutoRun
        Original = $original; OriginalKind = $kind
    } | ConvertTo-Json -Compress
    # Save the prior value before modifying AutoRun; retain it throughout updates.
    New-ItemProperty -LiteralPath $RegistryPath -Name $stateName -Value $newState -PropertyType String -Force | Out-Null
    New-ItemProperty -LiteralPath $RegistryPath -Name AutoRun -Value $updated -PropertyType $kind -Force | Out-Null
}
