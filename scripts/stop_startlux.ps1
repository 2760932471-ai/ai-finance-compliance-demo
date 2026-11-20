$ErrorActionPreference='Stop'
$auditRoot=Split-Path $PSScriptRoot -Parent
$auditRecord=Join-Path $auditRoot 'data/startlux/processes.json'
if(-not (Test-Path -LiteralPath $auditRecord)){Write-Output 'No managed StartLux processes';exit 0}
$auditEntries=@(Get-Content -LiteralPath $auditRecord -Raw | ConvertFrom-Json)
[array]::Reverse($auditEntries)
foreach($auditEntry in $auditEntries){
 $auditProcess=Get-CimInstance Win32_Process -Filter "ProcessId=$([int]$auditEntry.pid)"
 if($auditProcess -and $auditProcess.ExecutablePath -eq $auditEntry.path -and $auditProcess.CommandLine.Contains($auditEntry.marker)){Stop-Process -Id ([int]$auditEntry.pid);Write-Output ('Stopped managed StartLux process '+$auditEntry.pid)}
}
