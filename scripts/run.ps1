param(
  [Parameter(Mandatory = $true, Position = 0)]
  [ValidateSet("doctor", "once", "start", "stop", "status", "arm", "disarm", "clear-stop")]
  [string]$Command,
  [switch]$Arm
)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$env:PYTHONPATH = $root
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"
chcp 65001 | Out-Null
$cmd = Get-Command python -ErrorAction SilentlyContinue
if (-not $cmd) { throw "python is not on PATH. Use PATH Python 3.13, not the Hermes runtime." }
$py = $cmd.Source
if ($py.ToLower().Contains("\hermes\")) { throw "Refusing the Hermes bundled Python." }
$argList = @("-m", "worker", $Command)
if ($Arm) { $argList += "--arm" }
& $py @argList
exit $LASTEXITCODE
