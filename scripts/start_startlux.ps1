param([switch]$CheckOnly)
$ErrorActionPreference='Stop'
$auditRoot=Split-Path $PSScriptRoot -Parent
$auditRuntime=Join-Path $auditRoot 'data/startlux'
$auditModel=Join-Path $auditRuntime 'StartLux-Decision-0.8B-Q8_0-GGUF'
$auditPython=Join-Path $auditRuntime '.venv/Scripts/python.exe'
$auditLlama=Get-ChildItem -LiteralPath (Join-Path $auditRuntime 'llama') -Filter llama-server.exe -Recurse -File | Select-Object -First 1
if(-not $auditLlama -or -not (Test-Path -LiteralPath $auditPython) -or -not (Test-Path -LiteralPath (Join-Path $auditModel 'StartLux-Decision-0.8B-Q8_0.gguf'))){throw 'StartLux assets missing. Run scripts/install_startlux.py and install the isolated runtime first.'}
if($CheckOnly){Write-Output 'StartLux CPU runtime assets present';exit 0}
$auditRecords=@()
$auditRecordPath=Join-Path $auditRuntime 'processes.json'
if(Test-Path -LiteralPath $auditRecordPath){$auditRecords=@(Get-Content -LiteralPath $auditRecordPath -Raw | ConvertFrom-Json)}
$auditLlamaReady=$false
try {$auditHealth=Invoke-RestMethod 'http://127.0.0.1:8081/health' -TimeoutSec 2;$auditLlamaReady=($auditHealth.status -eq 'ok')}catch{}
if(-not $auditLlamaReady){
 $auditArgs=@('-m',('"'+(Join-Path $auditModel 'StartLux-Decision-0.8B-Q8_0.gguf')+'"'),'--host','127.0.0.1','--port','8081','-ngl','0','-c','32768','--parallel','1','--threads','4','--threads-batch','4')
 $auditChild=Start-Process -FilePath $auditLlama.FullName -ArgumentList $auditArgs -WorkingDirectory $auditLlama.DirectoryName -WindowStyle Hidden -RedirectStandardOutput (Join-Path $auditRuntime 'llama.log') -RedirectStandardError (Join-Path $auditRuntime 'llama-error.log') -PassThru
 $auditRecords+=@{pid=$auditChild.Id;path=$auditLlama.FullName;marker=$auditModel}
 $auditRecords | ConvertTo-Json | Set-Content -LiteralPath $auditRecordPath -Encoding UTF8
 for($i=0;$i -lt 30;$i++){try {$auditHealth=Invoke-RestMethod 'http://127.0.0.1:8081/health' -TimeoutSec 1;if($auditHealth.status -eq 'ok'){$auditLlamaReady=$true;break}}catch{};Start-Sleep -Milliseconds 500}
 if(-not $auditLlamaReady){throw 'llama.cpp did not become ready. See data/startlux/llama-error.log.'}
}
$auditReady=$false
try {$auditHealth=Invoke-RestMethod 'http://127.0.0.1:8090/health' -TimeoutSec 2;$auditReady=($auditHealth.model -eq 'StartLux-Decision-0.8B-Q8_0-GGUF')}catch{}
if(-not $auditReady){
 $env:HF_HUB_OFFLINE='1';$env:HF_HUB_DISABLE_TELEMETRY='1';$env:PYTHONUTF8='1';$env:OMP_NUM_THREADS='4'
 $auditArgs=@('-m','startlux_decision.gguf_server','--model-dir',('"'+$auditModel+'"'),'--llama','http://127.0.0.1:8081','--port','8090','--workers','1','--name','StartLux-Decision-0.8B-Q8_0-GGUF')
 $auditChild=Start-Process -FilePath $auditPython -ArgumentList $auditArgs -WorkingDirectory (Join-Path $auditRuntime 'source') -WindowStyle Hidden -RedirectStandardOutput (Join-Path $auditRuntime 'decision.log') -RedirectStandardError (Join-Path $auditRuntime 'decision-error.log') -PassThru
 $auditRecords+=@{pid=$auditChild.Id;path=$auditPython;marker='startlux_decision.gguf_server'}
 $auditRecords | ConvertTo-Json | Set-Content -LiteralPath $auditRecordPath -Encoding UTF8
 for($i=0;$i -lt 30;$i++){try {$auditHealth=Invoke-RestMethod 'http://127.0.0.1:8090/health' -TimeoutSec 1;if($auditHealth.model -eq 'StartLux-Decision-0.8B-Q8_0-GGUF'){$auditReady=$true;break}}catch{};Start-Sleep -Milliseconds 500}
 if(-not $auditReady){throw 'StartLux did not become ready. See data/startlux/decision-error.log.'}
}
Write-Output 'StartLux CPU decision service ready: http://127.0.0.1:8090/v1/systemone'
