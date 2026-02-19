# Save notes to current agent's context file
param(
    [Parameter(Mandatory=$true)]
    [string]$Message,

    [Parameter(Mandatory=$false)]
    [string]$Agent
)

$ErrorActionPreference = "Stop"

# Determine which agent's context to update
if ($Agent) {
    $alfredRoot = $env:ALFRED_ROOT ?? (Split-Path -Parent $PSScriptRoot)
    $configDir = Join-Path $alfredRoot ".claude-$Agent"
} elseif ($env:CLAUDE_CONFIG_DIR) {
    $configDir = $env:CLAUDE_CONFIG_DIR
} else {
    Write-Host "No agent specified and CLAUDE_CONFIG_DIR not set. Using conductor by default." -ForegroundColor Yellow
    $alfredRoot = $env:ALFRED_ROOT ?? (Split-Path -Parent $PSScriptRoot)
    $configDir = Join-Path $alfredRoot ".claude-conductor"
}

$contextFile = Join-Path $configDir ".claudecontext"

if (-not (Test-Path $contextFile)) {
    Write-Host "Error: Context file not found at $contextFile" -ForegroundColor Red
    exit 1
}

$timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
$entry = "`n## Note - $timestamp`n`n$Message`n"

Add-Content -Path $contextFile -Value $entry

Write-Host "[OK] Saved note to $configDir" -ForegroundColor Green
Write-Host "     $Message" -ForegroundColor Cyan
