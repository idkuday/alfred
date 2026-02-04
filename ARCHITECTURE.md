# System Architecture

## Overview
The "Home Ai" system is a local-first smart home assistant that integrates with Home Assistant and is extensible via plugins. It uses a multi-agent system ("The Forge") to generate new capabilities on the fly.

## Core Components

### 1. AI Server (FastAPI)
The central nervous system.
- **Main**: Exposes REST endpoints (`/execute`, `/transcribe`, `/devices`).
- **Integration Layer**: Connects to Home Assistant and loads generic Plugins.
- **Alfred Router**: A semantic router (using Qwen 2.5 3B via Ollama) that decides which tool to call based on user input.

### 2. The Forge (Agentic Loop)
A meta-agent system responsible for creating *new* capabilities (plugins).
Implemented as a `LangGraph` state machine.

```mermaid
graph TD
    User[User Requirement] -->|Task Description| R[Researcher Agent]
    
    subgraph "The Forge (LangGraph)"
        R -->|Research Notes| C[Coder Agent]
        C -->|Draft Code| Rev[Reviewer Agent]
        Rev -->|Feedback| C
        Rev -->|Approved| P[Publisher Agent]
    end
    
    P -->|Write File| Plugins(ai_server/plugins/)
    Plugins -->|Auto-Load| App[AI Server / Alfred Router]
```

### 3. Alfred Router Flow
How user commands are processed.

```mermaid
sequenceDiagram
    participant U as User
    participant S as Server
    participant AR as Alfred Router
    participant P as Plugin/Tool
    
    U->>S: "Calculate square root of 144"
    S->>AR: route("Calculate square root of 144")
    AR->>AR: Analyze Tools
    AR-->>S: CallTool(tool="math_plugin", params={...})
    S->>P: execute(params)
    P-->>S: Result
    S-->>U: Response
```

## Directory Structure
- `ai_server/`
  - `main.py`: Entry point.
  - `alfred_router/`: Semantic routing logic.
  - `forge/`: Agent definitions (Researcher, Coder, Reviewer).
  - `plugins/`: Directory where new skills are dropped.
  - `integration/`: Core Home Assistant logic.
