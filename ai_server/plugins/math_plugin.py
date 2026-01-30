from ai_server.integration.base import DeviceIntegration
from ai_server.models import Command, CommandResponse, DeviceInfo
from typing import Optional, List, Dict, Any
import math

class SquareRootCalculatorDeviceIntegration(DeviceIntegration):
    async def execute_command(self, command: Command) -> CommandResponse:
        if command.action == "square_root":
            number = command.parameters.get("number", 0)
            result = math.sqrt(number)
            return CommandResponse(
                status="success",
                action=command.action,
                target=command.target,
                message=f"Result: {result}",
                device_state={"result": result}
            )
        return CommandResponse(
            status="error",
            action=command.action,
            target=command.target,
            error=f"Unknown action: {command.action}"
        )

    async def get_device_info(self, entity_id: str) -> Optional[DeviceInfo]:
        return None

    async def discover_devices(self) -> List[DeviceInfo]:
        return []

    async def health_check(self) -> bool:
        return True

{
  "test_scenario": {
    "action": "square_root",
    "parameters": {"number": 16},
    "expected_result_contains": "Result: 4.0"
  }
}