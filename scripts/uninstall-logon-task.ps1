$ErrorActionPreference = "Stop"
schtasks /Delete /TN AgentHubWorker /F
Write-Output "Removed AgentHubWorker. Local state under %LOCALAPPDATA%\AgentHub\worker was left in place."
