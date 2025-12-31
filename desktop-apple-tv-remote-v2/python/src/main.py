#!/usr/bin/env python3
"""
pyatv-server: JSON-RPC server for Apple TV control via stdin/stdout.
"""

import asyncio
import sys
import signal
from server import JsonRpcServer
from connection import ConnectionManager


async def main():
    """Main entry point for the pyatv server."""
    # Create server first (with None connection_manager)
    # Then create connection_manager with emit callback
    # Finally set connection_manager on server
    server = JsonRpcServer(None)
    connection_manager = ConnectionManager(emit_callback=server.emit_event)
    server.connection_manager = connection_manager
    
    # Initialize storage for credential persistence
    await connection_manager.initialize()
    
    # Setup signal handlers for graceful shutdown
    loop = asyncio.get_running_loop()
    
    def shutdown_handler():
        asyncio.create_task(server.shutdown())
    
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, shutdown_handler)
        except NotImplementedError:
            # Windows doesn't support add_signal_handler
            pass
    
    # Emit ready event
    await server.emit_event("ready", {"version": "0.1.0"})
    
    # Run the server
    await server.run()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
    except Exception as e:
        # Log to stderr for debugging
        print(f"Fatal error: {e}", file=sys.stderr)
        sys.exit(1)
