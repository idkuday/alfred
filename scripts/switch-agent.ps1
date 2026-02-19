# Interactive agent switcher
$ErrorActionPreference = "Stop"

$alfredRoot = $env:ALFRED_ROOT ?? (Split-Path -Parent $PSScriptRoot)
$alfredUiRoot = $env:ALFRED_UI_ROOT ?? (Join-Path (Split-Path -Parent $alfredRoot) "alfred-ui")

$agents = @(
    @{Name="conductor"; Command="claude-alfred-conductor"; Dir=".claude-conductor"; Project="alfred"; ProjectPath=$alfredRoot; Desc="Main coordinator - project root"},
    @{Name="router"; Command="claude-alfred-router"; Dir=".claude-router"; Project="alfred"; ProjectPath=$alfredRoot; Desc="Alfred Router specialist - alfred_router/"},
    @{Name="forge"; Command="claude-alfred-forge"; Dir=".claude-forge"; Project="alfred"; ProjectPath=$alfredRoot; Desc="Forge specialist - forge/"},
    @{Name="integration"; Command="claude-alfred-integration"; Dir=".claude-integration"; Project="alfred"; ProjectPath=$alfredRoot; Desc="Integration specialist - integration/"},
    @{Name="api"; Command="claude-alfred-api"; Dir=".claude-api"; Project="alfred"; ProjectPath=$alfredRoot; Desc="API specialist - main.py, models.py"},
    @{Name="ui"; Command="claude-alfred-ui"; Dir=".claude-ui"; Project="alfred-ui"; ProjectPath=$alfredUiRoot; Desc="UI specialist - React frontend"}
)

Write-Host "`nAvailable Agents:" -ForegroundColor Cyan
Write-Host "=================" -ForegroundColor Cyan

for ($i = 0; $i -lt $agents.Count; $i++) {
    $agent = $agents[$i]
    Write-Host "[$($i+1)] $($agent.Project.PadRight(12)) $($agent.Name.PadRight(15)) - $($agent.Desc)"
}

Write-Host ""
$choice = Read-Host "Select agent (1-$($agents.Count))"

$index = [int]$choice - 1
if ($index -ge 0 -and $index -lt $agents.Count) {
    $selected = $agents[$index]
    $configPath = Join-Path $selected.ProjectPath $selected.Dir

    $env:CLAUDE_CONFIG_DIR = $configPath

    Write-Host "`n[OK] Switched to $($selected.Name) agent" -ForegroundColor Green
    Write-Host "  Project: $($selected.Project)" -ForegroundColor Gray
    Write-Host "  Config: $configPath" -ForegroundColor Gray
    Write-Host "  Scope: $($selected.Desc)" -ForegroundColor Gray
    Write-Host "`nRun '$($selected.Command)' or 'claude --agent $($selected.Name)' to start a session." -ForegroundColor Yellow
} else {
    Write-Host "Invalid selection." -ForegroundColor Red
}
