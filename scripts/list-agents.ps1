# List all available agents and their status
$ErrorActionPreference = "Stop"

$alfredRoot = $env:ALFRED_ROOT ?? (Split-Path -Parent $PSScriptRoot)
$alfredUiRoot = $env:ALFRED_UI_ROOT ?? (Join-Path (Split-Path -Parent $alfredRoot) "alfred-ui")

$agents = @(
    @{Name="conductor"; Command="claude-alfred-conductor"; Dir=".claude-conductor"; Project="alfred"; Scope="Project root"; Primary="Coordination"},
    @{Name="router"; Command="claude-alfred-router"; Dir=".claude-router"; Project="alfred"; Scope="alfred_router/"; Primary="Semantic routing"},
    @{Name="forge"; Command="claude-alfred-forge"; Dir=".claude-forge"; Project="alfred"; Scope="forge/"; Primary="Plugin generation"},
    @{Name="integration"; Command="claude-alfred-integration"; Dir=".claude-integration"; Project="alfred"; Scope="integration/"; Primary="Device integrations"},
    @{Name="api"; Command="claude-alfred-api"; Dir=".claude-api"; Project="alfred"; Scope="main.py, models.py"; Primary="FastAPI endpoints"},
    @{Name="ui"; Command="claude-alfred-ui"; Dir=".claude-ui"; Project="alfred-ui"; Scope="src/"; Primary="React frontend"}
)

Write-Host "`nAlfred Multi-Agent Development System" -ForegroundColor Cyan
Write-Host "=====================================" -ForegroundColor Cyan

$currentAgent = if ($env:CLAUDE_CONFIG_DIR) {
    $agents | Where-Object { $env:CLAUDE_CONFIG_DIR -like "*$($_.Dir)*" } | Select-Object -First 1 -ExpandProperty Name
} else {
    $null
}

Write-Host "`nProject      Agent            Scope                   Primary Responsibility" -ForegroundColor White
Write-Host "--------------------------------------------------------------------------------" -ForegroundColor Gray

foreach ($agent in $agents) {
    $projectRoot = if ($agent.Project -eq "alfred") { $alfredRoot } else { $alfredUiRoot }
    $configPath = Join-Path $projectRoot $agent.Dir
    $exists = Test-Path $configPath

    $statusIcon = if ($exists) { "[OK]" } else { "[  ]" }
    $statusColor = if ($exists) { "Green" } else { "Red" }

    $isCurrent = $agent.Name -eq $currentAgent
    $marker = if ($isCurrent) { ">" } else { " " }

    $line = "$marker $($agent.Project.PadRight(12)) $($agent.Name.PadRight(16)) $($agent.Scope.PadRight(23)) $($agent.Primary.PadRight(27))"

    if ($isCurrent) {
        Write-Host $line -ForegroundColor Yellow -NoNewline
        Write-Host " $statusIcon" -ForegroundColor $statusColor
    } else {
        Write-Host $line -NoNewline
        Write-Host " $statusIcon" -ForegroundColor $statusColor
    }
}

Write-Host ""

if ($currentAgent) {
    Write-Host "Current Agent: " -NoNewline -ForegroundColor White
    Write-Host "$currentAgent" -ForegroundColor Yellow
} else {
    Write-Host "No agent currently active" -ForegroundColor Gray
}

Write-Host "`nCommands:" -ForegroundColor Cyan
Write-Host "  claude-alfred-<agent>   Start an alfred backend agent" -ForegroundColor White
Write-Host "  claude-alfred-ui        Start the alfred-ui frontend agent" -ForegroundColor White
Write-Host "  claude-switch           Interactive agent switcher" -ForegroundColor White
Write-Host "  Save-ClaudeContext      Save notes to current agent" -ForegroundColor White
Write-Host ""
