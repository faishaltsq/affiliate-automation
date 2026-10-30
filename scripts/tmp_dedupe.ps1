$targets = @(
    @{ Name = 'v2_runtime'; Needle = 'src\.v2_runtime' }
    @{ Name = 'bot'; Needle = 'main\.py bot' }
)

foreach ($t in $targets) {
    $procs = @(Get-CimInstance Win32_Process |
        Where-Object { $_.Name -match '^python(w)?\.exe$' -and $_.CommandLine -match $t.Needle } |
        Sort-Object CreationDate)

    if ($procs.Count -le 1) {
        Write-Output "$($t.Name): $($procs.Count) instance(s) - nothing to do"
        continue
    }

    # Keep the OLDEST live instance; stop the newer duplicates.
    $keep = $procs[0]
    foreach ($p in $procs[1..($procs.Count - 1)]) {
        Write-Output "$($t.Name): stopping duplicate PID $($p.ProcessId) (keeping $($keep.ProcessId))"
        Stop-Process -Id $p.ProcessId -Force
    }
}

Start-Sleep -Seconds 2
$remaining = @(Get-CimInstance Win32_Process |
    Where-Object { $_.Name -match '^python(w)?\.exe$' -and $_.CommandLine -match 'v2_runtime|main\.py bot' } |
    Select-Object ProcessId, CommandLine)
Write-Output "Remaining:"
$remaining | ForEach-Object { Write-Output "  PID $($_.ProcessId): $($_.CommandLine)" }
