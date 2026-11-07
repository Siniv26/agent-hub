param(
  [Parameter(Mandatory = $true)]
  [string]$WorkerRoot
)
$ErrorActionPreference = "Stop"
if ($WorkerRoot -match "[&|<>^' ]") { throw "WorkerRoot must not contain spaces or shell characters" }
$run = Join-Path $WorkerRoot "scripts\run.ps1"
if (-not (Test-Path -LiteralPath $run)) { throw "missing run.ps1" }
$tr = "powershell.exe -NoProfile -ExecutionPolicy Bypass -File `"$run`" start"
schtasks /Create /TN AgentHubWorker /SC ONLOGON /RL LIMITED /IT /F /TR $tr
Write-Output "Installed AgentHubWorker ONLOGON Interactive Limited. It does not arm the worker and does not start a model."
