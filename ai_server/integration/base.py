"""
Base integration layer for device control.
All device integrations should inherit from DeviceIntegration.
"""
from abc import ABC, abstractmethod
from typing import Dict, Any, Optional, List
from ..models import Command, CommandResponse, DeviceInfo


class DeviceIntegration(ABC):
    """Abstract base class for device integrations."""
    
    def __init__(self, config: Dict[str, Any] = None):
        """Initialize integration with configuration."""
        self.config = config or {}
        self.name = self.__class__.__name__
    
    @abstractmethod
    async def execute_command(self, command: Command) -> CommandResponse:
        """
        Execute a command on a device.
        
        Args:
            command: Command to execute
            
        Returns:
            CommandResponse with execution result
        """
        pass
    
    @abstractmethod
    async def get_device_info(self, entity_id: str) -> Optional[DeviceInfo]:
        """
        Get information about a device.
        
        Args:
            entity_id: Device identifier
            
        Returns:
            DeviceInfo or None if device not found
        """
        pass
    
    @abstractmethod
    async def discover_devices(self) -> List[DeviceInfo]:
        """
        Discover available devices.
        
        Returns:
            List of discovered devices
        """
        pass
    
    @abstractmethod
    async def health_check(self) -> bool:
        """
        Check if integration is healthy and connected.
        
        Returns:
            True if healthy, False otherwise
        """
        pass
    
    def normalize_entity_id(self, target: str) -> str:
        """
        Normalize target string to entity_id format.
        Override in subclasses for custom normalization.
        
        Args:
            target: User-friendly target name
            
        Returns:
            Normalized entity_id
        """
        # Default: replace spaces with underscores, lowercase
        return target.lower().replace(" ", "_").replace("-", "_")



