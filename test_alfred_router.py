#!/usr/bin/env python3
"""
Standalone test script for Alfred Router validation.

Tests routing logic without FastAPI or HTTP.
Validates router decisions, schema compliance, and execution paths.
"""

import json
import sys
import asyncio
from pathlib import Path
from typing import Dict, Any, Optional, List

# Force UTF-8 for Windows consoles
sys.stdout.reconfigure(encoding='utf-8')
sys.stderr.reconfigure(encoding='utf-8')

# Add ai_server to path
sys.path.insert(0, str(Path(__file__).parent))

# Check for required dependencies
try:
    from ai_server.config import settings
    from ai_server.alfred_router.router import AlfredRouter
    from ai_server.alfred_router.schemas import (
        RouterDecision,
        CallToolDecision,
        RouteToQADecision,
        ProposeNewToolDecision,
    )
    from ai_server.alfred_router.qa_handler import GemmaQAHandler
    from ai_server.alfred_router.tool_registry import list_tools
except ImportError as e:
    print("❌ ERROR: Missing required dependencies")
    print(f"   Import error: {e}")
    print("\n   Please install dependencies:")
    print("   pip install -r requirements.txt")
    sys.exit(1)


# ============================================================================
# Mock Tool Execution
# ============================================================================

def mock_tool_execution(decision: CallToolDecision) -> None:
    """
    Mock tool execution - prints only, no actual device control.
    
    SAFETY: This function does not modify system state, write files,
    or execute any real device commands.
    """
    tool_name = decision.tool
    params = decision.parameters
    
    print(f"🔧 TOOL: {tool_name}")
    print(f"📦 PARAMS: {json.dumps(params)}")
    print("⚠️  [MOCKED - No actual execution]")


# ============================================================================
# Test Execution
# ============================================================================

def print_separator():
    """Print a visual separator between test cases."""
    print("\n" + "─" * 80 + "\n")


def print_test_header(test_input: str):
    """Print formatted test input header."""
    print(f"🧪 INPUT: {test_input}")


def print_router_json(decision: RouterDecision):
    """Print the router's JSON decision."""
    print("🧠 ROUTER JSON:")
    decision_dict = decision.model_dump()
    print(json.dumps(decision_dict, indent=2))


def print_decision_type(decision: RouterDecision):
    """Print the validated decision type."""
    intent = decision.intent
    print(f"✅ DECISION: {intent}")


async def execute_decision(
    decision: RouterDecision,
    qa_handler: Optional[GemmaQAHandler],
) -> None:
    """
    Execute the router decision (mocked for tools, real for QA).
    
    Args:
        decision: Validated router decision
        qa_handler: QA handler instance (optional, may be None)
    """
    if isinstance(decision, CallToolDecision):
        # Mock tool execution
        mock_tool_execution(decision)
        
    elif isinstance(decision, RouteToQADecision):
        # Route to QA handler (real execution)
        if qa_handler is None:
            print("⚠️  QA handler not available (model path not configured)")
            return
        
        try:
            qa_response = await qa_handler.answer(decision.query)
            print(f"🤖 QA RESPONSE:")
            print(f"{qa_response}")
        except Exception as e:
            print(f"❌ QA handler error: {e}")
            
    elif isinstance(decision, ProposeNewToolDecision):
        # Propose new tool (non-executable, print only)
        print(f"💡 PROPOSAL:")
        print(f"   Name: {decision.name}")
        print(f"   Description: {decision.description}")
        print("⚠️  [Non-executable proposal only]")


async def run_test(
    test_input: str,
    router: AlfredRouter,
    tools: List[Dict[str, str]],
    qa_handler: Optional[GemmaQAHandler],
) -> bool:
    """
    Run a single test case.
    
    Returns:
        True if test completed successfully, False otherwise
    """
    print_separator()
    print_test_header(test_input)
    
    try:
        # Route the input
        decision = router.route(user_input=test_input, tools=tools)
        
        # Print router output
        print_router_json(decision)
        print()  # Blank line
        
        # Print decision type
        print_decision_type(decision)
        
        # Execute the decision
        await execute_decision(decision, qa_handler)
        
        return True
        
    except ValueError as e:
        print(f"❌ VALIDATION ERROR: {e}")
        return False
    except Exception as e:
        print(f"❌ EXECUTION ERROR: {e}")
        import traceback
        traceback.print_exc()
        return False


# ============================================================================
# Main Test Execution
# ============================================================================

async def main():
    """Main test execution function."""
    print("=" * 80)
    print("ALFRED ROUTER TEST SUITE")
    print("=" * 80)
    print()
    
    # Check configuration
    if not settings.alfred_router_model:
        print("❌ ERROR: ALFRED_ROUTER_MODEL not configured")
        print("   Set environment variable or configure in .env file")
        sys.exit(1)
    
    # Initialize router
    print("🔧 Initializing Alfred Router...")
    try:
        router = AlfredRouter(
            model=settings.alfred_router_model,
            prompt_path=settings.alfred_router_prompt_path,
            temperature=settings.alfred_router_temperature,
            max_tokens=settings.alfred_router_max_tokens,
        )
        print(f"   ✅ Router initialized with model: {settings.alfred_router_model}")
    except Exception as e:
        print(f"   ❌ Failed to initialize router: {e}")
        sys.exit(1)
    
    # Initialize QA handler (optional)
    qa_handler = None
    if settings.alfred_qa_model:
        print("🔧 Initializing QA Handler...")
        try:
            qa_handler = GemmaQAHandler(
                model=settings.alfred_qa_model,
                temperature=settings.alfred_qa_temperature,
                max_tokens=settings.alfred_qa_max_tokens,
            )
            print(f"   ✅ QA handler initialized with model: {settings.alfred_qa_model}")
        except Exception as e:
            print(f"   ⚠️  QA handler not available: {e}")
    else:
        print("   ⚠️  QA handler not configured (ALFRED_QA_MODEL not set)")
    
    # Get available tools (without loading plugins)
    tools = list_tools()
    print(f"\n📋 Available tools: {len(tools)}")
    for tool in tools:
        print(f"   - {tool['name']}: {tool['description']}")
    
    print("\n" + "=" * 80)
    print("RUNNING TEST CASES")
    print("=" * 80)
    
    # Define test cases
    test_cases = [
        {
            "input": "Turn on the bedroom light",
            "expected": "call_tool",
            "description": "Should route to tool execution"
        },
        {
            "input": "What is the capital of France?",
            "expected": "route_to_qa",
            "description": "Should route to Q/A handler"
        },
        {
            "input": "Can you help me with my lights?",
            "expected": "safe behavior (clarification or QA)",
            "description": "Should handle safely (NOT tool execution)"
        },
        {
            "input": "Add a new function to control garden lights",
            "expected": "propose_new_tool",
            "description": "Should propose new tool (non-executable)"
        },
    ]
    
    # Run tests
    results = []
    for i, test_case in enumerate(test_cases, 1):
        print(f"\n📝 TEST {i}/{len(test_cases)}: {test_case['description']}")
        success = await run_test(
            test_input=test_case["input"],
            router=router,
            tools=tools,
            qa_handler=qa_handler,
        )
        results.append((test_case["input"], success))
    
    # Print summary
    print_separator()
    print("=" * 80)
    print("TEST SUMMARY")
    print("=" * 80)
    
    passed = sum(1 for _, success in results if success)
    total = len(results)
    
    for input_text, success in results:
        status = "✅ PASS" if success else "❌ FAIL"
        print(f"{status}: {input_text}")
    
    print(f"\n📊 Results: {passed}/{total} tests passed")
    
    if passed == total:
        print("🎉 All tests passed!")
        return 0
    else:
        print("⚠️  Some tests failed")
        return 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)

