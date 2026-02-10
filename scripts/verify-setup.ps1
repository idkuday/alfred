# Verify Alfred Multi-Agent Setup
$ErrorActionPreference = "Stop"

$alfredRoot = "C:\Users\udayr\Documents\Projects\alfred"

Write-Host "`n╔════════════════════════════════════════════════════════════════════╗" -ForegroundColor Cyan
Write-Host "║     Alfred Multi-Agent Setup Verification                       ║" -ForegroundColor Cyan
Write-Host "╚════════════════════════════════════════════════════════════════════╝`n" -ForegroundColor Cyan

$checksPassed = 0
$totalChecks = 0

function Test-Item-Exists {
    param($Path, $Description)

    $script:totalChecks++
    if (Test-Path $Path) {
        Write-Host "✓" -NoNewline -ForegroundColor Green
        Write-Host " $Description" -ForegroundColor White
        $script:checksPassed++
        return $true
    } else {
        Write-Host "✗" -NoNewline -ForegroundColor Red
        Write-Host " $Description" -ForegroundColor White
        Write-Host "  Missing: $Path" -ForegroundColor Gray
        return $false
    }
}

Write-Host "Checking Agent Directories..." -ForegroundColor Yellow
Write-Host "─────────────────────────────" -ForegroundColor Gray

Test-Item-Exists "$alfredRoot\.claude-conductor" "Conductor agent directory"
Test-Item-Exists "$alfredRoot\.claude-router" "Router agent directory"
Test-Item-Exists "$alfredRoot\.claude-forge" "Forge agent directory"
Test-Item-Exists "$alfredRoot\.claude-integration" "Integration agent directory"
Test-Item-Exists "$alfredRoot\.claude-api" "API agent directory"

Write-Host "`nChecking Context Files..." -ForegroundColor Yellow
Write-Host "─────────────────────────" -ForegroundColor Gray

Test-Item-Exists "$alfredRoot\.claude-conductor\.claudecontext" "Conductor context file"
Test-Item-Exists "$alfredRoot\.claude-router\.claudecontext" "Router context file"
Test-Item-Exists "$alfredRoot\.claude-forge\.claudecontext" "Forge context file"
Test-Item-Exists "$alfredRoot\.claude-integration\.claudecontext" "Integration context file"
Test-Item-Exists "$alfredRoot\.claude-api\.claudecontext" "API context file"

Write-Host "`nChecking Helper Scripts..." -ForegroundColor Yellow
Write-Host "──────────────────────────" -ForegroundColor Gray

Test-Item-Exists "$alfredRoot\scripts\save-context.ps1" "Save context script"
Test-Item-Exists "$alfredRoot\scripts\switch-agent.ps1" "Switch agent script"
Test-Item-Exists "$alfredRoot\scripts\list-agents.ps1" "List agents script"

Write-Host "`nChecking Documentation..." -ForegroundColor Yellow
Write-Host "─────────────────────────" -ForegroundColor Gray

Test-Item-Exists "$alfredRoot\AGENT_COORDINATION.md" "Agent coordination log"
Test-Item-Exists "$alfredRoot\MULTI_AGENT_SETUP.md" "Setup guide"
Test-Item-Exists "$alfredRoot\AGENT_QUICK_REFERENCE.md" "Quick reference"

Write-Host "`nChecking PowerShell Profile..." -ForegroundColor Yellow
Write-Host "──────────────────────────────" -ForegroundColor Gray

$profileExists = Test-Item-Exists $PROFILE "PowerShell profile exists"

if ($profileExists) {
    $profileContent = Get-Content $PROFILE -Raw
    $totalChecks++
    if ($profileContent -match "claude-conductor") {
        Write-Host "✓" -NoNewline -ForegroundColor Green
        Write-Host " Agent functions loaded in profile" -ForegroundColor White
        $checksPassed++
    } else {
        Write-Host "✗" -NoNewline -ForegroundColor Red
        Write-Host " Agent functions NOT found in profile" -ForegroundColor White
        Write-Host "  Run: . `$PROFILE to reload" -ForegroundColor Gray
    }
}

Write-Host "`nChecking PowerShell Functions..." -ForegroundColor Yellow
Write-Host "────────────────────────────────" -ForegroundColor Gray

$functions = @(
    "claude-conductor",
    "claude-router",
    "claude-forge",
    "claude-integration",
    "claude-api",
    "goto-alfred",
    "goto-router",
    "goto-forge",
    "goto-integration",
    "goto-plugins",
    "Save-ClaudeContext",
    "claude-switch",
    "list-agents"
)

foreach ($func in $functions) {
    $totalChecks++
    if (Get-Command $func -ErrorAction SilentlyContinue) {
        Write-Host "✓" -NoNewline -ForegroundColor Green
        Write-Host " $func" -ForegroundColor White
        $checksPassed++
    } else {
        Write-Host "✗" -NoNewline -ForegroundColor Red
        Write-Host " $func (not loaded - reload profile)" -ForegroundColor White
    }
}

Write-Host "`n═══════════════════════════════════════════════════════════════════" -ForegroundColor Cyan
Write-Host " Results: $checksPassed/$totalChecks checks passed" -ForegroundColor White

if ($checksPassed -eq $totalChecks) {
    Write-Host "═══════════════════════════════════════════════════════════════════" -ForegroundColor Cyan
    Write-Host "`n✓ SETUP COMPLETE!" -ForegroundColor Green
    Write-Host "`nNext Steps:" -ForegroundColor Yellow
    Write-Host "  1. Open a new PowerShell window (to load functions)" -ForegroundColor White
    Write-Host "  2. Run: list-agents" -ForegroundColor White
    Write-Host "  3. Start an agent: claude-conductor" -ForegroundColor White
    Write-Host "`nDocumentation:" -ForegroundColor Yellow
    Write-Host "  Full guide: alfred\MULTI_AGENT_SETUP.md" -ForegroundColor White
    Write-Host "  Quick ref:  alfred\AGENT_QUICK_REFERENCE.md" -ForegroundColor White
} else {
    Write-Host "═══════════════════════════════════════════════════════════════════" -ForegroundColor Cyan
    Write-Host "`n⚠ SETUP INCOMPLETE" -ForegroundColor Yellow
    Write-Host "`nTo fix:" -ForegroundColor Yellow
    Write-Host "  1. Open a new PowerShell window" -ForegroundColor White
    Write-Host "  2. Or reload profile: . `$PROFILE" -ForegroundColor White
    Write-Host "  3. Run this script again to verify" -ForegroundColor White
}

Write-Host ""
