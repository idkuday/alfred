"""
Plugin system for extensible device handlers and commands.
"""
import importlib
import pkgutil
import logging
from pathlib import Path
from typing import Dict, Type, List, Optional
from ..config import settings
from ..integration.base import DeviceIntegration

logger = logging.getLogger(__name__)


class PluginManager:
    """Manages loading and registration of plugins."""

    def __init__(self, plugins_dir: Optional[str] = None):
        # Default is this package's own directory, not the process cwd: a relative
        # "plugins" path found zero plugins whenever Alfred was started outside the
        # repo root (omni-jev plan J21).
        self.plugins_dir = Path(plugins_dir) if plugins_dir else Path(__file__).parent
        self.integrations: Dict[str, DeviceIntegration] = {}
        self.plugins: Dict[str, any] = {}
    
    def load_plugins(self):
        """Dynamically load all plugins from plugins directory."""
        if not self.plugins_dir.exists():
            logger.warning(f"Plugins directory {self.plugins_dir} does not exist")
            return
        
        # Load integration plugins
        for module_info in pkgutil.iter_modules([str(self.plugins_dir)]):
            if module_info.ispkg:
                continue
            
            try:
                module_name = f"{__name__}.{module_info.name}"
                module = importlib.import_module(module_name)
                
                # Look for DeviceIntegration subclasses
                for attr_name in dir(module):
                    attr = getattr(module, attr_name)
                    if (isinstance(attr, type) and 
                        issubclass(attr, DeviceIntegration) and 
                        attr != DeviceIntegration):
                        integration = attr()
                        self.integrations[integration.name] = integration
                        logger.info(f"Loaded integration plugin: {integration.name}")
                
                # Store plugin module
                self.plugins[module_info.name] = module
                
            except Exception as e:
                logger.error(f"Failed to load plugin {module_info.name}: {e}", exc_info=True)
    
    def get_integration(self, name: str) -> DeviceIntegration:
        """Get integration by name."""
        return self.integrations.get(name)
    
    def list_integrations(self) -> List[str]:
        """List all loaded integration names."""
        return list(self.integrations.keys())


# Global plugin manager instance. settings.plugins_dir defaults to this package's
# directory (config.py), so PLUGINS_DIR still overrides it. No import cycle: config.py
# imports nothing from ai_server.
plugin_manager = PluginManager(settings.plugins_dir)



