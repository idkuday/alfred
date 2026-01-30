"""
Home Assistant integration for device control.
"""
import aiohttp
import logging
from typing import Dict, Any, Optional, List
from ..models import Command, CommandResponse, DeviceInfo, Action
from ..config import settings
from .base import DeviceIntegration

logger = logging.getLogger(__name__)


class HomeAssistantIntegration(DeviceIntegration):
    """Integration for Home Assistant API."""
    
    def __init__(self, config: Dict[str, Any] = None):
        super().__init__(config)
        self.base_url = config.get("url", settings.ha_url) if config else settings.ha_url
        self.token = config.get("token", settings.ha_token) if config else settings.ha_token
        self.session: Optional[aiohttp.ClientSession] = None
    
    async def _get_session(self) -> aiohttp.ClientSession:
        """Get or create aiohttp session."""
        if self.session is None or self.session.closed:
            headers = {}
            if self.token:
                headers["Authorization"] = f"Bearer {self.token}"
            self.session = aiohttp.ClientSession(
                base_url=self.base_url,
                headers=headers
            )
        return self.session
    
    async def _close_session(self):
        """Close aiohttp session."""
        if self.session and not self.session.closed:
            await self.session.close()
    
    async def health_check(self) -> bool:
        """Check if Home Assistant is accessible."""
        try:
            session = await self._get_session()
            async with session.get("/api/") as response:
                return response.status == 200
        except Exception as e:
            logger.error(f"Health check failed: {e}")
            return False
    
    async def execute_command(self, command: Command) -> CommandResponse:
        """Execute command via Home Assistant API."""
        try:
            entity_id = self.normalize_entity_id(command.target)
            
            # Map action to HA service
            service_map = {
                Action.TURN_ON: "turn_on",
                Action.TURN_OFF: "turn_off",
                Action.TOGGLE: "toggle",
                Action.SET_BRIGHTNESS: "turn_on",  # HA uses turn_on with brightness
                Action.GET_STATUS: None,  # Special case
            }
            
            action_lower = command.action.lower()
            
            # Handle get_status separately
            if action_lower == Action.GET_STATUS or action_lower == "get_status":
                device_info = await self.get_device_info(entity_id)
                if device_info:
                    return CommandResponse(
                        status="success",
                        action=command.action,
                        target=command.target,
                        message=f"Status retrieved for {command.target}",
                        device_state={
                            "state": device_info.state,
                            "attributes": device_info.attributes
                        }
                    )
                else:
                    return CommandResponse(
                        status="error",
                        action=command.action,
                        target=command.target,
                        error=f"Device {command.target} not found"
                    )
            
            # Get service name
            service = service_map.get(action_lower, action_lower)
            domain = entity_id.split(".")[0] if "." in entity_id else "homeassistant"
            
            # Prepare service data
            service_data = {"entity_id": entity_id}
            if command.parameters:
                service_data.update(command.parameters)
            
            # Special handling for brightness
            if action_lower == Action.SET_BRIGHTNESS and command.parameters:
                brightness = command.parameters.get("brightness")
                if brightness:
                    service_data["brightness"] = brightness
            
            # Call HA service
            session = await self._get_session()
            url = f"/api/services/{domain}/{service}"
            
            async with session.post(url, json=service_data) as response:
                if response.status == 200:
                    result = await response.json()
                    logger.info(f"HA command executed: {service} on {entity_id}")
                    
                    # Get updated device state
                    device_info = await self.get_device_info(entity_id)
                    device_state = None
                    if device_info:
                        device_state = {
                            "state": device_info.state,
                            "attributes": device_info.attributes
                        }
                    
                    return CommandResponse(
                        status="success",
                        action=command.action,
                        target=command.target,
                        message=f"Successfully executed {command.action} on {command.target}",
                        device_state=device_state
                    )
                else:
                    error_text = await response.text()
                    logger.error(f"HA API error: {response.status} - {error_text}")
                    return CommandResponse(
                        status="error",
                        action=command.action,
                        target=command.target,
                        error=f"Home Assistant API error: {response.status}"
                    )
        
        except Exception as e:
            logger.error(f"Error executing command: {e}", exc_info=True)
            return CommandResponse(
                status="error",
                action=command.action,
                target=command.target,
                error=str(e)
            )
    
    async def get_device_info(self, entity_id: str) -> Optional[DeviceInfo]:
        """Get device information from Home Assistant."""
        try:
            session = await self._get_session()
            url = f"/api/states/{entity_id}"
            
            async with session.get(url) as response:
                if response.status == 200:
                    data = await response.json()
                    return DeviceInfo(
                        entity_id=data.get("entity_id"),
                        name=data.get("attributes", {}).get("friendly_name", entity_id),
                        device_type=entity_id.split(".")[0],
                        state=data.get("state"),
                        attributes=data.get("attributes", {})
                    )
                else:
                    logger.warning(f"Device {entity_id} not found: {response.status}")
                    return None
        except Exception as e:
            logger.error(f"Error getting device info: {e}")
            return None
    
    async def discover_devices(self) -> List[DeviceInfo]:
        """Discover all devices from Home Assistant."""
        try:
            session = await self._get_session()
            url = "/api/states"
            
            async with session.get(url) as response:
                if response.status == 200:
                    states = await response.json()
                    devices = []
                    for state in states:
                        entity_id = state.get("entity_id")
                        # Filter to relevant domains (lights, switches, etc.)
                        domain = entity_id.split(".")[0] if "." in entity_id else ""
                        if domain in ["light", "switch", "fan", "climate", "cover"]:
                            devices.append(DeviceInfo(
                                entity_id=entity_id,
                                name=state.get("attributes", {}).get("friendly_name", entity_id),
                                device_type=domain,
                                state=state.get("state"),
                                attributes=state.get("attributes", {})
                            ))
                    return devices
                else:
                    logger.error(f"Failed to discover devices: {response.status}")
                    return []
        except Exception as e:
            logger.error(f"Error discovering devices: {e}")
            return []
    
    async def __aenter__(self):
        """Async context manager entry."""
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit."""
        await self._close_session()



