"""
Shared state for The Forge agents.
"""
from typing import TypedDict, List, Optional, Dict, Any

class ForgeState(TypedDict):
    """
    Represents the current state of the plugin generation task.
    """
    # Inputs
    task_name: str
    task_description: str
    
    # Internal artifacts
    research_notes: Optional[str]
    code_draft: Optional[str]
    file_name: Optional[str]
    
    # Feedback loop
    review_comments: List[str]
    test_error: Optional[str]
    test_scenario: Optional[Dict[str, Any]] # Feedback from the Tester node
    iteration_count: int
    
    # Output status
    status: str  # "researching", "coding", "testing", "reviewing", "complete", "failed"
