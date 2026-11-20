$ErrorActionPreference='Stop'
$auditRoot=Split-Path $PSScriptRoot -Parent
$auditPython=Join-Path $env:USERPROFILE '.cache/codex-runtimes/codex-primary-runtime/dependencies/python/python.exe'
if(-not (Test-Path -LiteralPath $auditPython)){$auditPython=(Get-Command python -ErrorAction Stop).Source}
$auditVenv=Join-Path $auditRoot 'data/startlux/.venv'
$auditRuntimePython=Join-Path $auditVenv 'Scripts/python.exe'
& $auditPython (Join-Path $PSScriptRoot 'install_startlux.py')
if($LASTEXITCODE -ne 0){throw 'Official asset download failed'}
& $auditPython -m venv $auditVenv
if($LASTEXITCODE -ne 0){throw 'Isolated runtime creation failed'}
& $auditRuntimePython -m pip install --no-cache-dir --index-url https://download.pytorch.org/whl/cpu torch==2.11.0
if($LASTEXITCODE -ne 0){throw 'CPU PyTorch install failed'}
& $auditRuntimePython -m pip install --no-cache-dir transformers==5.8.1 'pillow>=10'
if($LASTEXITCODE -ne 0){throw 'Tokenizer dependencies install failed'}
& (Join-Path $PSScriptRoot 'start_startlux.ps1')
Write-Output 'Configure the audit application using docs/StartLux本机接入记录.md. Existing .env has not been overwritten.'
