import asyncio
import os
import sys
from pathlib import Path

# Add src to sys.path
sys.path.append(str(Path("src").absolute()))

from ems_mcp.api.client import EMSClient
from ems_mcp.config import get_settings

async def main():
    settings = get_settings()
    print(f"Base URL: {settings.base_url}")
    print(f"Username: {settings.username}")
    
    async with EMSClient.create() as client:
        try:
            systems = await client.get("/api/v2/ems-systems")
            print("Successfully retrieved EMS systems:")
            for sys in systems:
                print(f"  - {sys.get('name')} (ID: {sys.get('id')})")
        except Exception as e:
            print(f"Error: {e}")

if __name__ == "__main__":
    asyncio.run(main())
