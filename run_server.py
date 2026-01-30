#!/usr/bin/env python3
"""
Convenience script to run the AI Server.
"""
import uvicorn
from ai_server.config import settings

if __name__ == "__main__":
    uvicorn.run(
        "ai_server.main:app",
        host=settings.host,
        port=settings.port,
        reload=settings.reload,
        log_level=settings.log_level.lower()
    )



