"""
Integration test for The Forge.
"""
import sys
import logging

# Configure logging
logging.basicConfig(level=logging.INFO)

try:
    from ai_server.forge.graph import forge_graph
except ImportError:
    # Fix import path if running from root
    import sys
    import os
    sys.path.append(os.getcwd())
    from ai_server.forge.graph import forge_graph

def test_forge():
    print("🔥 Igniting The Forge...")
    
    # Clean up previous run
    import os
    plugin_path = os.path.join("ai_server", "plugins", "math_plugin.py")
    if os.path.exists(plugin_path):
        os.remove(plugin_path)
        print("🧹 Cleaned up old math_plugin.py")

    initial_state = {
        "task_name": "Math Plugin",
        "task_description": "Create a plugin that calculates the square root of a number given in 'parameters'.",
        "iteration_count": 0,
        "review_comments": [],
        "status": "start"
    }
    
    print(f"📥 Input: {initial_state['task_description']}")
    
    # helper to print streaming output
    try:
        for output in forge_graph.stream(initial_state):
            for key, value in output.items():
                print(f"\n📍 Node '{key}' finished.")
                if "status" in value:
                     print(f"   Status: {value['status']}")
                if "code_draft" in value:
                    print("   📄 Code Generated (Preview):")
                    print("   " + value["code_draft"][:100].replace("\n", " ") + "...")
                if "review_comments" in value and value["review_comments"]:
                     print(f"   ⚠️ Review Feedback: {value['review_comments']}")
                if "published_path" in value:
                    print(f"   💾 Published to: {value['published_path']}")

        print("\n✅ Forge processing complete!")
        
        # VERIFICATION
        if not os.path.exists(plugin_path):
             print(f"❌ Error: Plugin file not found at {plugin_path}")
             return

        print("\n🔎 Verifying Plugin Loading...")
        from ai_server.plugins import plugin_manager
        from pathlib import Path
        
        # dynamic fix for test environment running from root
        plugin_manager.plugins_dir = Path("ai_server/plugins")
        
        plugin_manager.load_plugins()
        
        integrations = plugin_manager.list_integrations()
        
        # Dynamic check: Read the file to find the class name
        import re
        with open(plugin_path, "r", encoding="utf-8") as f:
            content = f.read()
            match = re.search(r"class\s+(\w+)\s*\(DeviceIntegration\):", content)
            if match:
                expected_class_name = match.group(1)
                print(f"🔎 Expecting plugin class: '{expected_class_name}'")
            else:
                print("⚠️ Could not parse class name from file, falling back to manual search.")
                expected_class_name = None
        
        found_plugin = None
        if expected_class_name and expected_class_name in integrations:
            found_plugin = expected_class_name
        
        if not found_plugin:
             print(f"❌ Error: Expected plugin '{expected_class_name}' not found in loaded integrations.")
             print(f"Loaded: {integrations}")
             return
        
        print("✅ Plugin loaded successfully.")
        
        print("\n🧠 Testing Alfred Router with new plugin...")
        from ai_server.config import settings
        from ai_server.alfred_router.router import AlfredRouter
        from ai_server.alfred_router.tool_registry import list_tools
        
        router = AlfredRouter(
            model=settings.alfred_router_model,
            prompt_path=settings.alfred_router_prompt_path,
            temperature=settings.alfred_router_temperature,
        )
        
        query = "Calculate the square root of 144"
        print(f"❓ Asking Router: '{query}'")
        decision = router.route(query, list_tools())
        
        print(f"👉 Decision: {decision}")
        
        if decision.tool == found_plugin:
             print(f"✅ SUCCESS: Router selected the new plugin '{found_plugin}'!")
             
             # NOW EXECUTE IT using Router's parameters + plugin's action
             print(f"\n🚀 Executing command on {found_plugin}...")
             plugin_instance = plugin_manager.integrations[found_plugin]
             
             # Extract the action name from the generated code (find 'if command.action == "xxx"')
             action_match = re.search(r'if\s+command\.action\s*==\s*["\']([^"\']+)["\']', content)
             if action_match:
                 action = action_match.group(1)
                 print(f"📋 Found action in plugin: '{action}'")
             else:
                 action = "calculate_square_root"
                 print(f"⚠️ Could not find action, using default: '{action}'")
             
             # Use the Router's ACTUAL parsed parameters from the query "square root of 144"
             # The Router should have extracted something like {'number': '144'}
             router_params = decision.parameters or {}
             print(f"📋 Router parsed parameters: {router_params}")
             
             # Convert string numbers to actual numbers for math operations
             params = {}
             for k, v in router_params.items():
                 try:
                     params[k] = float(v) if '.' in str(v) else int(v)
                 except:
                     params[k] = v
             
             # Create a Command object with CORRECT action + ROUTER's parameters
             from ai_server.models import Command
             cmd = Command(
                 action=action,
                 target="system",
                 parameters=params
             )
             
             # Execute
             import asyncio
             try:
                 response = asyncio.run(plugin_instance.execute_command(cmd))
                 print(f"🎉 Execution Result: {response}")
             except Exception as exec_err:
                 print(f"❌ Execution Failed: {exec_err}")
                 import traceback
                 traceback.print_exc()

        else:
             print(f"❌ FAILURE: Router selected '{decision.tool}' instead of '{found_plugin}'")

        
    except Exception as e:
        print(f"\n❌ Forge crashed: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    test_forge()
