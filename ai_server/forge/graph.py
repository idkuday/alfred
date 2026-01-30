"""
LangGraph definition for The Forge.
"""
from langgraph.graph import StateGraph, END
from .state import ForgeState
from .agents import research_node, coding_node, review_node, tester_node

def should_continue(state: ForgeState) -> str:
    """
    Determine the next node based on status.
    """
    status = state["status"]
    iteration = state.get("iteration_count", 0)
    
    # Fail-safe: if we've been through too many coding iterations, give up
    MAX_ITERATIONS = 5
    if iteration >= MAX_ITERATIONS and status == "coding":
        print(f"Forge: Max iterations ({MAX_ITERATIONS}) reached. Aborting.")
        return END
    
    if status == "coding":
        return "coder"
    elif status == "testing":
        return "tester"
    elif status == "reviewing":
        return "reviewer"
    elif status == "complete":
        return "publisher"
    else:
        return END

def publisher_node(state: ForgeState):
    """
    Final node: Writes the generated code to a file in the plugins directory.
    """
    import os
    import re
    
    # Generate filename from task name (snake_case)
    # e.g. "Math Plugin" -> "math_plugin.py"
    task_name = state["task_name"]
    clean_name = re.sub(r'[^a-zA-Z0-9]', '_', task_name.lower())
    clean_name = re.sub(r'_+', '_', clean_name).strip('_')
    filename = f"{clean_name}.py"
    
    # Path to plugins directory
    plugins_dir = os.path.join("ai_server", "plugins")
    if not os.path.exists(plugins_dir):
        os.makedirs(plugins_dir, exist_ok=True)
        
    file_path = os.path.join(plugins_dir, filename)
    
    # Write the code
    with open(file_path, "w", encoding="utf-8") as f:
        f.write(state["code_draft"])
        
    print(f"Forge: Published plugin to {file_path}")
    
    return {
        "status": "finished",
        "published_path": file_path
    }

# Define the graph
workflow = StateGraph(ForgeState)

# Add nodes
workflow.add_node("researcher", research_node)
workflow.add_node("coder", coding_node)
workflow.add_node("tester", tester_node)
workflow.add_node("reviewer", review_node)
workflow.add_node("publisher", publisher_node)

# Add edges
# Start -> Researcher
workflow.set_entry_point("researcher")

# Researcher -> Coder
workflow.add_edge("researcher", "coder")

# Coder -> Tester
workflow.add_edge("coder", "tester")

# Tester -> Conditional (Coder or Reviewer)
workflow.add_conditional_edges(
    "tester",
    should_continue,
    {
        "coder": "coder",
        "reviewer": "reviewer"
    }
)

# Reviewer -> Conditional (Coder or Publisher)
workflow.add_conditional_edges(
    "reviewer",
    should_continue,
    {
        "coder": "coder",
        "publisher": "publisher",
        END: END
    }
)

# Publisher -> End
workflow.add_edge("publisher", END)

# Compile
forge_graph = workflow.compile()
