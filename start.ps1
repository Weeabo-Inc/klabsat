# PhoneWatch dashboard launcher (Samsung state.json + iPhone iphone.json).
#
#   powershell -File dashboard\start.ps1
#   powershell -File dashboard\start.ps1 -Port 8791
#   powershell -File dashboard\start.ps1 -Iphone dashboard\sample-iphone.json        # sample data
#   powershell -File dashboard\start.ps1 -Iphone dashboard\sample-iphone-locked.json # sample data
#
# Prints the exact URL to open, then serves in the foreground until Ctrl+C.
param(
    [int]$Port = 8791,
    [string]$State   = "<REPO_ROOT>\phonewatch\state.json",
    [string]$History = "<REPO_ROOT>\phonewatch\history.jsonl",
    [string]$Iphone  = "<REPO_ROOT>\phonewatch\iphone.json",
    [string]$Python  = "python"
)

$ErrorActionPreference = 'Continue'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$dash = Join-Path $here 'dashboard.py'

if (-not (Test-Path $dash)) { Write-Output "FATAL: dashboard.py not found next to start.ps1 ($dash)"; exit 2 }

# ---- resolve -Iphone relative to cwd, the repo root, then this folder ------
if ($Iphone -and -not [System.IO.Path]::IsPathRooted($Iphone) -and -not (Test-Path $Iphone)) {
    $cands = @(
        (Join-Path (Split-Path -Parent $here) $Iphone),
        (Join-Path $here $Iphone),
        (Join-Path $here (Split-Path -Leaf $Iphone))
    )
    foreach ($c in $cands) { if (Test-Path $c) { $Iphone = $c; break } }
}
if ($Iphone -and (Test-Path $Iphone)) { $Iphone = (Resolve-Path $Iphone).Path }

Write-Output "=== PhoneWatch dashboard ==="
Write-Output ("  python     : {0}" -f (& $Python --version 2>&1))
Write-Output ("  state.json : {0}  ({1})" -f $State,   $(if (Test-Path $State)   { 'found' } else { 'NOT FOUND yet' }))
Write-Output ("  history    : {0}  ({1})" -f $History, $(if (Test-Path $History) { 'found' } else { 'NOT FOUND yet' }))
Write-Output ("  iphone.json: {0}  ({1})" -f $Iphone,  $(if (Test-Path $Iphone)  { 'found' } else { 'NOT FOUND yet' }))
if ($Iphone -like '*sample-iphone*') {
    Write-Output "  NOTE       : that iphone.json is SAMPLE data, not a live capture."
}

# ---- is the port already taken? -------------------------------------------
$u = "http://127.0.0.1:$Port/"
$busy = $false
try { $null = Invoke-WebRequest -Uri $u -UseBasicParsing -TimeoutSec 2; $busy = $true } catch { $busy = $false }
try {
    if (-not $busy -and (Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue)) { $busy = $true }
} catch { }

if ($busy) {
    Write-Output ""
    Write-Output ("  PORT {0} IS ALREADY SERVING SOMETHING." -f $Port)
    Write-Output "  An older dashboard build there will NOT have the iPhone card."
    Write-Output ("  * to use the running copy, open {0}" -f $u)
    Write-Output ("  * to use THIS build, either stop that process first or run:")
    Write-Output ("      powershell -File `"$($MyInvocation.MyCommand.Path)`" -Port $($Port + 1)")
    exit 3
}

# ---- launch (foreground; Ctrl+C stops it) ---------------------------------
Write-Output ""
Write-Output ("  URL        : {0}" -f $u)
Write-Output ("  endpoints  : /api/state  /api/history  /api/iphone  /api/health")
Write-Output "  Ctrl+C to stop."
Write-Output ""
& $Python $dash --port $Port --state $State --history $History --iphone $Iphone
exit $LASTEXITCODE
