"""
Agent Node Definitions for The Forge.
"""
import logging
from typing import Dict, Any

# We reuse the existing OllamaLLM wrapper from main router, 
# or instantiate a new one if we want different settings.
from langchain_ollama import OllamaLLM
from ai_server.config import settings
from .state import ForgeState
from .prompts import RESEARCHER_PROMPT, CODER_PROMPT, REVIEWER_PROMPT

logger = logging.getLogger(__name__)

# Initialize the LLM for the Forge
# using the same model as router for now, but could be different
llm = OllamaLLM(
    model=settings.alfred_router_model, 
    temperature=0.2,
    num_predict=1024
)

def research_node(state: ForgeState) -> Dict[str, Any]:
    """
    Researcher Agent: Analyzes the task and produces notes.
    """
    logger.info(f"Forge: Researching '{state['task_name']}'...")
    
    prompt = RESEARCHER_PROMPT.format(task_description=state["task_description"])
    notes = llm.invoke(prompt)
    
    return {"research_notes": str(notes), "status": "coding"}

def coding_node(state: ForgeState) -> Dict[str, Any]:
    """
    Coder Agent: Writes the Python code based on notes.
    """
    logger.info(f"Forge: Coding '{state['task_name']}'...")
    
    # Implement simple RAG: Read the base class definition to provide ground truth
    try:
        # Assuming running from root
        with open("ai_server/integration/base.py", "r", encoding="utf-8") as f:
            interface_definition = f.read()
    except Exception as e:
        logger.warning(f"Forge: Could not read interface definition: {e}")
        interface_definition = "Could not load source code."

    prompt = CODER_PROMPT.format(
        research_notes=state["research_notes"],
        interface_definition=interface_definition,
        review_comments="\n".join(state.get("review_comments", [])) or "None"
    )
    raw_output = str(llm.invoke(prompt))
    
    # Extract JSON Test Scenario
    import json
    import re
    
    test_scenario = None
    code = raw_output
    
    # Look for JSON block at the end
    json_match = re.search(r"```json(.*?)```", raw_output, re.DOTALL)
    if json_match:
        try:
            json_str = json_match.group(1).strip()
            data = json.loads(json_str)
            if "test_scenario" in data:
                test_scenario = data["test_scenario"]
            # Remove the JSON block from the code
            code = raw_output.replace(json_match.group(0), "")
        except Exception as e:
            logger.warning(f"Forge: Failed to parse test scenario JSON: {e}")
    
    # Fallback: Look for Python dict assignment in output (LLM often outputs this way)
    if not test_scenario:
        py_dict_match = re.search(r'test_scenario\s*=\s*(\{.*\})', raw_output, re.DOTALL)
        if py_dict_match:
            try:
                # Use ast.literal_eval for safe Python dict parsing
                import ast
                outer_dict = ast.literal_eval(py_dict_match.group(1))
                if "test_scenario" in outer_dict:
                    test_scenario = outer_dict["test_scenario"]
                # Remove the dict from the code
                code = raw_output.replace(py_dict_match.group(0), "")
            except Exception as e:
                logger.warning(f"Forge: Failed to parse test scenario as Python dict: {e}")

    # Simple cleaning of markdown blocks
    code = code.strip()
    if code.startswith("```python"):
        code = code.replace("```python", "", 1)
        if code.endswith("```"):
            code = code[:-3]
    elif code.startswith("```"):
        code = code.replace("```", "", 1)
        if code.endswith("```"):
            code = code[:-3]
        
    return {
        "code_draft": code.strip(),
        "test_scenario": test_scenario,
        "iteration_count": state["iteration_count"] + 1,
        "status": "testing"
    }

def tester_node(state: ForgeState) -> Dict[str, Any]:
    """
    Tester Agent: Tries to compile/exec the code to catch runtime errors.
    """
    logger.info(f"Forge: Testing iteration {state['iteration_count']}...")
    code = state["code_draft"]
    
    try:
        # 1. Syntax Check
        compile(code, "<string>", "exec")
        
        # 2. Runtime/Import Check (sandboxed-ish)
        # We need to mock the environment slightly so it doesn't actually fly
        # but verifies imports exist.
        import builtins
        allowed_globals = {"__builtins__": builtins}
        exec(code, allowed_globals)
        
        # 3. Instantiation Check (Catch logic errors in __init__)
        # Look for the class defined in this code
        from ai_server.integration.base import DeviceIntegration
        class_found = False
        for name, obj in allowed_globals.items():
            if (isinstance(obj, type) and 
                issubclass(obj, DeviceIntegration) and 
                obj is not DeviceIntegration):
                
                # Try to instantiate it
                logger.info(f"Forge: Testing instantiation of {name}...")
                try:
                    instance = obj()
                    class_found = True
                    logger.info(f"Forge: Instantiation of {name} successful.")
                    
                    # 4. Interface Compliance Check
                    if not hasattr(instance, "name"):
                         raise Exception(f"Instance of {name} is missing 'name' attribute. Did you call super().__init__()?")
                    
                    # We could also check for methods here
                    
                except Exception as inst_e:
                    raise Exception(f"Instantiation/Compliance failed for {name}: {inst_e}")
                
                # 5. Behavioral Verification (The "Muscle" Check)
                if state.get("test_scenario"):
                    scenario = state["test_scenario"]
                    logger.info(f"Forge: Running Behavioral Test: {scenario}")
                    
                    try:
                        # Construct the Command Object (Type Enforcement)
                        from ai_server.models import Command
                        cmd = Command(
                            action=scenario["action"],
                            parameters=scenario.get("parameters", {}),
                            target="system" # Dummy target
                        )
                        
                        # Execute (Async execution in sync context requires helper)
                        import asyncio
                        
                        # We must run the async method. 
                        # Since we are in a sync node, we use asyncio.run 
                        # (assuming this node isn't already inside a loop, which LangGraph usually is)
                        # NOTE: If LangGraph runs async, we should await. 
                        # But for now assuming sync node execution.
                        try:
                            # Try to run it
                            loop = asyncio.new_event_loop()
                            asyncio.set_event_loop(loop)
                            result = loop.run_until_complete(instance.execute_command(cmd))
                            loop.close()
                            
                            logger.info(f"Forge: Test Result: {result}")
                            
                            # Verify Result
                            expected = scenario.get("expected_result_contains")
                            if expected:
                                result_str = str(result)
                                if expected not in result_str:
                                    raise Exception(f"Behavioral Test Failed. Expected '{expected}' in result, got: {result_str}")
                                    
                        except TypeError as te:
                             if "unhashable type: 'Command'" in str(te):
                                 raise Exception("Logic Error: You are treating the 'Command' object as a dictionary key. Use 'command.action' instead.")
                             raise te
                        except Exception as exec_e:
                            raise Exception(f"Execution failed: {exec_e}")

                    except Exception as behavioral_e:
                        raise Exception(f"Behavioral Verification Failed: {behavioral_e}")
                    
        if not class_found:
             logger.warning("Forge: No DeviceIntegration subclass found in code.")
             # We might want to fail here, but maybe it's a helper file? 
             # For now, let's warn but pass if syntax is ok.
        
        logger.info("Forge: Test Passed.")
        return {"status": "reviewing", "test_error": None}
        
    except Exception as e:
        error_msg = f"Tester: Execution failed with {type(e).__name__}: {str(e)}"
        logger.warning(error_msg)
        return {
            "status": "coding", # loop back to fixer
            "test_error": error_msg,
            # We append to review comments so the coder sees it history
            "review_comments": state["review_comments"] + [error_msg]
        }

def review_node(state: ForgeState) -> Dict[str, Any]:
    """
    Reviewer Agent: Checks the code.
    """
    logger.info(f"Forge: Reviewing iteration {state['iteration_count']}...")
    
    prompt = REVIEWER_PROMPT.format(code_draft=state["code_draft"])
    feedback = str(llm.invoke(prompt))
    
    if "APPROVED" in feedback:
        logger.info("Forge: Code APPROVED.")
        return {"status": "complete", "review_comments": []}
    else:
        logger.info("Forge: Issues found in code.")
        return {
            "status": "coding", # loop back
            "review_comments": [feedback] 
            # In a real loop, we'd append this to history for the Coder to see
        }
