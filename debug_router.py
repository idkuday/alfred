import sys
from ai_server.config import settings
from ai_server.alfred_router.router import AlfredRouter
from ai_server.alfred_router.tool_registry import list_tools

# Force UTF-8
sys.stdout.reconfigure(encoding='utf-8')
sys.stderr.reconfigure(encoding='utf-8')

def main():
    print(f"Model: {settings.alfred_router_model}")
    router = AlfredRouter(
        model=settings.alfred_router_model,
        prompt_path=settings.alfred_router_prompt_path,
        temperature=settings.alfred_router_temperature,
        max_tokens=settings.alfred_router_max_tokens,
    )
    
    tools = list_tools()
    user_input = "Turn on the bedroom light"
    
    print(f"Input: {user_input}")
    raw_output = router.llm.invoke(router._render_prompt(user_input, tools))
    if not isinstance(raw_output, str):
        raw_output = str(raw_output)
    cleaned = raw_output.strip()
    
    with open("router_output.txt", "w", encoding="utf-8") as f:
        f.write(cleaned)
        
    try:
        decision = router.route(user_input=user_input, tools=tools)
        print("Success!")
        print(decision)
    except Exception as e:
        print("Error caught:")
        print(f"Type: {type(e)}")
        print(f"Message: {e}")
        with open("validation_error.txt", "w", encoding="utf-8") as f:
            f.write(str(e))
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    main()
