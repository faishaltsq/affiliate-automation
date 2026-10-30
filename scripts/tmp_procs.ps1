$items = @(Get-CimInstance Win32_Process | Where-Object { $_.Name -match '^python(w)?\.exe$' -and $_.CommandLine -match 'v2_runtime|main\.py|v2_cli' })
ConvertTo-Json -InputObject @($items | Select-Object ProcessId, Name, CreationDate, CommandLine) -Compress
