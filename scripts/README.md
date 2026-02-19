# Alfred Multi-Agent Helper Scripts

This directory contains PowerShell helper scripts for the Alfred multi-agent development workflow.

## Scripts

### list-agents.ps1
Lists all available agents with their status and current active agent.

```powershell
.\scripts\list-agents.ps1
# Or from profile:
list-agents
```

**Output:**
- Shows all 5 agents
- Displays scope and responsibility
- Shows [OK] status for configured agents
- Highlights current active agent with `>`

### save-context.ps1
Saves notes to the current agent's context file with timestamp.

```powershell
.\scripts\save-context.ps1 -Message "Added new feature"
# Or from profile:
Save-ClaudeContext "Added new feature"

# Save to specific agent
.\scripts\save-context.ps1 -Message "Updated router" -Agent router
```

**Features:**
- Automatically detects current agent from $env:CLAUDE_CONFIG_DIR
- Falls back to conductor if no agent active
- Adds timestamp to notes
- Appends to .claudecontext file

### switch-agent.ps1
Interactive agent switcher with menu.

```powershell
.\scripts\switch-agent.ps1
# Or from profile:
claude-switch
```

**Features:**
- Shows numbered menu of all agents
- Displays scope for each agent
- Sets $env:CLAUDE_CONFIG_DIR
- Provides guidance on next steps

### verify-setup.ps1
Verifies the multi-agent setup is complete and correct.

```powershell
.\scripts\verify-setup.ps1
```

**Checks:**
- Agent directories exist
- Context files present
- Helper scripts available
- PowerShell profile configured
- Functions loaded and callable

## Usage from PowerShell Profile

All scripts are wrapped in PowerShell profile functions for easy access:

```powershell
# List agents
list-agents

# Save context note
Save-ClaudeContext "Your note here"

# Switch agents interactively
claude-switch
```

## Direct Agent Access

Instead of using the switcher, you can directly launch an agent:

```powershell
claude-conductor      # Main coordinator
claude-router         # Router specialist
claude-forge          # Forge specialist
claude-integration    # Integration specialist
claude-api            # API specialist
```

## Adding New Scripts

To add new helper scripts:

1. Create script in `alfred/scripts/`
2. Add function wrapper in PowerShell profile
3. Document in this README
4. Update MULTI_AGENT_SETUP.md if needed

## Script Template

```powershell
# Script description
param(
    [Parameter(Mandatory=$false)]
    [string]$YourParam
)

$ErrorActionPreference = "Stop"
$alfredRoot = $env:ALFRED_ROOT ?? (Split-Path -Parent $PSScriptRoot)

# Your script logic here

Write-Host "Done!" -ForegroundColor Green
```

## Environment Variables

Scripts use the following environment variables:

- `$env:CLAUDE_CONFIG_DIR` - Current agent's config directory
- `$PROFILE` - PowerShell profile path

## Error Handling

All scripts use `$ErrorActionPreference = "Stop"` for fail-fast behavior.

Common error messages:
- "Context file not found" - Agent directory not initialized
- "Script not found" - Helper script missing from alfred/scripts/
- "No agent currently active" - $env:CLAUDE_CONFIG_DIR not set

## Related Documentation

- **Setup Guide**: `../MULTI_AGENT_SETUP.md`
- **Quick Reference**: `../AGENT_QUICK_REFERENCE.md`
- **Coordination Log**: `../AGENT_COORDINATION.md`
