$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$root = Split-Path -Parent $here
if ((Split-Path -Leaf $root) -eq "scripts") { $root = Split-Path -Parent $root }
$sums = Join-Path $root "SHA256SUMS"
if (-not (Test-Path -LiteralPath $sums)) { throw "missing SHA256SUMS" }
Get-Content -LiteralPath $sums | ForEach-Object {
  if ($_ -match "^([A-Fa-f0-9]{64})\s+(.+)$") {
    $want = $Matches[1].ToUpper()
    $rel = $Matches[2]
    $path = Join-Path $root ($rel -replace "/", "\")
    if (-not (Test-Path -LiteralPath $path)) { throw "missing $rel" }
    $got = (Get-FileHash -Algorithm SHA256 -LiteralPath $path).Hash.ToUpper()
    if ($got -ne $want) { throw "hash mismatch $rel" }
  }
}
Write-Output "OK"
