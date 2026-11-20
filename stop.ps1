$ErrorActionPreference='Stop'
if(Test-Path -LiteralPath (Join-Path $PSScriptRoot 'data/startlux/processes.json')){& (Join-Path $PSScriptRoot 'scripts/stop_startlux.ps1')}
$auditRecord=Join-Path $PSScriptRoot 'data/server-process.json'
if (-not (Test-Path -LiteralPath $auditRecord)) {Write-Output 'No managed service record. Stop the terminal used to run server.py.';exit 0}
$auditSaved=Get-Content -LiteralPath $auditRecord -Raw | ConvertFrom-Json
$auditProcess=Get-CimInstance Win32_Process -Filter "ProcessId=$([int]$auditSaved.pid)"
$auditExpected=[IO.Path]::GetFullPath((Join-Path $PSScriptRoot 'server.py'))
if ($auditProcess -and $auditProcess.CommandLine.Contains($auditExpected)) {Stop-Process -Id ([int]$auditSaved.pid);Write-Output 'Service stopped. Saved data is retained.'}
else {Write-Output 'The recorded process is no longer this application.'}
