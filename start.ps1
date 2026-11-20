param([switch]$NoBrowser,[switch]$CheckOnly)
$ErrorActionPreference='Stop'
$auditRoot=$PSScriptRoot
$auditPythonCandidates=@((Join-Path $auditRoot '.venv/Scripts/python.exe'),(Join-Path $env:USERPROFILE '.cache/codex-runtimes/codex-primary-runtime/dependencies/python/python.exe'))
$auditPython=$auditPythonCandidates | Where-Object {Test-Path -LiteralPath $_} | Select-Object -First 1
if (-not $auditPython) { $auditCmd=Get-Command python -ErrorAction SilentlyContinue; if ($auditCmd) {$auditPython=$auditCmd.Source} }
if (-not $auditPython) {throw 'Python 3.11+ is required. See README.md.'}
& $auditPython -c 'import pypdf,pypdfium2,PIL,reportlab'
if ($LASTEXITCODE -ne 0) {throw 'Missing dependencies. Run: python -m pip install -r requirements.txt'}
if ($CheckOnly) {Write-Output 'Runtime and dependencies OK';exit 0}
$auditPort=8765
$auditEnv=Join-Path $auditRoot '.env'
$auditAutoDecision=$false
if (Test-Path -LiteralPath $auditEnv) {foreach($line in Get-Content -LiteralPath $auditEnv){if($line -match '^PORT=(\d+)$'){$auditPort=[int]$Matches[1]}}}
if (Test-Path -LiteralPath $auditEnv) {$auditAutoDecision=@(Get-Content -LiteralPath $auditEnv | Where-Object {$_ -match '^STARTLUX_AUTOSTART=on\s*$'}).Count -gt 0}
if($auditAutoDecision){& (Join-Path $auditRoot 'scripts/start_startlux.ps1')}
$auditUrl="http://127.0.0.1:$auditPort"
$auditReady=$false
try {$auditBoot=Invoke-RestMethod "$auditUrl/api/bootstrap" -TimeoutSec 2;$auditReady=($auditBoot.rules_version -eq 'T1-2026.10-v1')}catch{}
if (-not $auditReady) {
 $auditData=Join-Path $auditRoot 'data';New-Item -ItemType Directory -Path $auditData -Force | Out-Null
 $auditServer=Join-Path $auditRoot 'server.py'
 $auditProcess=Start-Process -FilePath $auditPython -ArgumentList @('"'+$auditServer+'"') -WorkingDirectory $auditRoot -WindowStyle Hidden -RedirectStandardOutput (Join-Path $auditData 'server.log') -RedirectStandardError (Join-Path $auditData 'server-error.log') -PassThru
 @{pid=$auditProcess.Id;server=$auditServer} | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $auditData 'server-process.json') -Encoding UTF8
 for($attempt=0;$attempt -lt 10;$attempt++){try {$auditBoot=Invoke-RestMethod "$auditUrl/api/bootstrap" -TimeoutSec 1;if($auditBoot.rules_version){$auditReady=$true;break}}catch{};Start-Sleep -Milliseconds 500}
 if(-not $auditReady){throw 'Service did not start. See data/server-error.log.'}
}
Write-Output "Financial Review is ready: $auditUrl"
if (-not $NoBrowser) {Start-Process -FilePath $auditUrl}
