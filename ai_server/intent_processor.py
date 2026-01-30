"""
Intent processing and command normalization.
Maps natural language commands to structured actions.

NOTE: In Alfred, this is an optional helper/tool. It must not run
automatically from the /execute path unless the router explicitly selects
it. The router remains the primary decision-maker.
"""
import logging
import re
from typing import Dict, Optional, Tuple
from .models import Command, Action

logger = logging.getLogger(__name__)


class IntentProcessor:
    """Processes and normalizes user commands."""
    
    # Action mappings (natural language -> action)
    ACTION_MAPPINGS = {
        "turn on": Action.TURN_ON,
        "switch on": Action.TURN_ON,
        "enable": Action.TURN_ON,
        "on": Action.TURN_ON,
        "turn off": Action.TURN_OFF,
        "switch off": Action.TURN_OFF,
        "disable": Action.TURN_OFF,
        "off": Action.TURN_OFF,
        "toggle": Action.TOGGLE,
        "set brightness": Action.SET_BRIGHTNESS,
        "brightness": Action.SET_BRIGHTNESS,
        "dim": Action.SET_BRIGHTNESS,
        "set color": Action.SET_COLOR,
        "color": Action.SET_COLOR,
        "status": Action.GET_STATUS,
        "state": Action.GET_STATUS,
    }
    
    # Room/device patterns
    ROOM_PATTERNS = [
        r"bedroom",
        r"living room",
        r"kitchen",
        r"bathroom",
        r"office",
        r"garage",
    ]
    
    def __init__(self, device_mappings: Dict[str, str] = None):
        """
        Initialize intent processor.
        
        Args:
            device_mappings: Dictionary mapping friendly names to entity_ids
        """
        self.device_mappings = device_mappings or {}
    
    def process(self, action: str, target: str, room: Optional[str] = None) -> Command:
        """
        Process raw command and return structured Command.
        
        Args:
            action: Raw action string
            target: Raw target string
            room: Optional room name
            
        Returns:
            Structured Command object
        """
        # Normalize action
        normalized_action = self._normalize_action(action)
        
        # Normalize target
        normalized_target = self._normalize_target(target, room)
        
        # Extract parameters (e.g., brightness, color)
        parameters = self._extract_parameters(action, target)
        
        # Determine intent
        intent = self._determine_intent(action, target)
        
        return Command(
            action=normalized_action,
            target=normalized_target,
            parameters=parameters,
            room=room or self._extract_room(target),
            intent=intent
        )
    
    def _normalize_action(self, action: str) -> str:
        """Normalize action string to standard action."""
        action_lower = action.lower().strip()
        
        # Check direct mappings
        for key, value in self.ACTION_MAPPINGS.items():
            if key in action_lower:
                return value.value
        
        # Default: return as-is (will be handled by integration)
        return action_lower
    
    def _normalize_target(self, target: str, room: Optional[str] = None) -> str:
        """Normalize target to entity_id format."""
        target_lower = target.lower().strip()
        
        # Check device mappings first
        if target_lower in self.device_mappings:
            return self.device_mappings[target_lower]
        
        # Build entity_id from room + target
        if room:
            return f"{room}_{target_lower}".replace(" ", "_")
        
        return target_lower.replace(" ", "_").replace("-", "_")
    
    def _extract_parameters(self, action: str, target: str) -> Optional[Dict]:
        """Extract parameters from command (brightness, color, etc.)."""
        params = {}
        
        # Extract brightness (0-100 or 0-255)
        brightness_match = re.search(r"(\d+)\s*(?:percent|%)", action.lower() + " " + target.lower())
        if brightness_match:
            brightness = int(brightness_match.group(1))
            # Convert 0-100 to 0-255 if needed
            if brightness <= 100:
                brightness = int((brightness / 100) * 255)
            params["brightness"] = brightness
        
        # Extract color (basic RGB or color names)
        color_match = re.search(r"(red|green|blue|yellow|orange|purple|white|warm|cool)", 
                               action.lower() + " " + target.lower())
        if color_match:
            params["color"] = color_match.group(1)
        
        return params if params else None
    
    def _extract_room(self, target: str) -> Optional[str]:
        """Extract room name from target string."""
        target_lower = target.lower()
        for pattern in self.ROOM_PATTERNS:
            if re.search(pattern, target_lower):
                return pattern.replace(" ", "_")
        return None
    
    def _determine_intent(self, action: str, target: str) -> str:
        """Determine high-level intent from command."""
        action_lower = action.lower()
        target_lower = target.lower()
        
        if any(word in action_lower for word in ["on", "enable", "activate"]):
            return "control_device_on"
        elif any(word in action_lower for word in ["off", "disable", "deactivate"]):
            return "control_device_off"
        elif "brightness" in action_lower or "dim" in action_lower:
            return "adjust_brightness"
        elif "color" in action_lower or "colour" in action_lower:
            return "change_color"
        elif "status" in action_lower or "state" in action_lower:
            return "query_status"
        else:
            return "control_device"

