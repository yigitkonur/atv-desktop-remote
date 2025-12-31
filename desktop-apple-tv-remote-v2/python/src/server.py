"""JSON-RPC 2.0 server implementation for stdin/stdout communication."""

import asyncio
import json
import os
import sys
import time
from typing import Any, Callable, Dict, Optional
from dataclasses import dataclass

from pyatv import exceptions as pyatv_exceptions
from errors import categorize_error, RETRYABLE_ERRORS, NON_RETRYABLE_ERRORS, PAIRING_ERRORS

try:
    import psutil
    HAS_PSUTIL = True
except ImportError:
    HAS_PSUTIL = False


@dataclass
class JsonRpcError:
    code: int
    message: str
    data: Optional[Any] = None

    def to_dict(self) -> Dict:
        result = {"code": self.code, "message": self.message}
        if self.data is not None:
            result["data"] = self.data
        return result


# Standard JSON-RPC error codes
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603

# Custom error codes
DEVICE_NOT_FOUND = -32001
CONNECTION_FAILED = -32002
NOT_CONNECTED = -32003
COMMAND_FAILED = -32004
PAIRING_FAILED = -32005


class JsonRpcServer:
    """Async JSON-RPC 2.0 server using stdin/stdout."""

    def __init__(self, connection_manager):
        self.connection_manager = connection_manager
        self._handlers: Dict[str, Callable] = {}
        self._running = False
        self._start_time = time.time()
        self._setup_handlers()

    def _setup_handlers(self):
        """Register all RPC method handlers."""
        self._handlers = {
            "health": self._handle_health,
            "scan": self._handle_scan,
            "connect": self._handle_connect,
            "disconnect": self._handle_disconnect,
            "remote_command": self._handle_remote_command,
            "start_pairing": self._handle_start_pairing,
            "finish_pairing": self._handle_finish_pairing,
            "get_status": self._handle_get_status,
            "list_saved_devices": self._handle_list_saved_devices,
            "forget_device": self._handle_forget_device,
            "set_text": self._handle_set_text,
            "clear_text": self._handle_clear_text,
            "get_text": self._handle_get_text,
            "cancel_reconnect": self._handle_cancel_reconnect,
            "system_wake": self._handle_system_wake,
        }

    async def run(self):
        """Main server loop - reads from stdin, writes to stdout."""
        self._running = True
        reader = asyncio.StreamReader()
        protocol = asyncio.StreamReaderProtocol(reader)
        
        loop = asyncio.get_running_loop()
        await loop.connect_read_pipe(lambda: protocol, sys.stdin)

        while self._running:
            try:
                line = await reader.readline()
                if not line:
                    break
                
                line_str = line.decode("utf-8").strip()
                if not line_str:
                    continue

                response = await self._process_request(line_str)
                if response:
                    await self._write_response(response)
                    
            except asyncio.CancelledError:
                break
            except Exception as e:
                error_response = self._error_response(
                    None, INTERNAL_ERROR, f"Server error: {e}"
                )
                await self._write_response(error_response)

    async def shutdown(self):
        """Graceful shutdown."""
        self._running = False
        await self.connection_manager.disconnect()

    async def emit_event(self, event: str, data: Any):
        """Emit an event to the Rust side."""
        notification = {
            "jsonrpc": "2.0",
            "method": "event",
            "params": {"event": event, "data": data},
        }
        print(f"[EMIT] {event}: {data}", file=sys.stderr)
        await self._write_response(notification)

    async def _emit_command_error(self, command: str, error_info: Dict):
        """Emit a structured command-error event for frontend handling."""
        await self.emit_event("command-error", {
            "command": command,
            "category": error_info.get("category", "unknown"),
            "type": error_info.get("type", "UnknownError"),
            "message": error_info.get("message", "An error occurred"),
            "action_required": error_info.get("action_required", "none"),
            "should_retry": error_info.get("should_retry", False),
            "technical_message": error_info.get("technical_message", ""),
        })

    async def _write_response(self, response: Dict):
        """Write JSON response to stdout."""
        line = json.dumps(response) + "\n"
        sys.stdout.write(line)
        sys.stdout.flush()

    async def _process_request(self, line: str) -> Optional[Dict]:
        """Process a single JSON-RPC request."""
        try:
            request = json.loads(line)
        except json.JSONDecodeError as e:
            return self._error_response(None, PARSE_ERROR, f"Parse error: {e}")

        if not isinstance(request, dict):
            return self._error_response(None, INVALID_REQUEST, "Request must be an object")

        jsonrpc = request.get("jsonrpc")
        if jsonrpc != "2.0":
            return self._error_response(None, INVALID_REQUEST, "Invalid JSON-RPC version")

        method = request.get("method")
        if not method or not isinstance(method, str):
            return self._error_response(None, INVALID_REQUEST, "Method must be a string")

        request_id = request.get("id")
        params = request.get("params", {})

        handler = self._handlers.get(method)
        if not handler:
            return self._error_response(request_id, METHOD_NOT_FOUND, f"Method not found: {method}")

        try:
            result = await handler(params)
            return self._success_response(request_id, result)
        except RETRYABLE_ERRORS as e:
            error_info = categorize_error(e)
            await self._emit_command_error(method, error_info)
            return self._error_response(request_id, CONNECTION_FAILED, error_info["message"], error_info)
        except NON_RETRYABLE_ERRORS as e:
            error_info = categorize_error(e)
            await self._emit_command_error(method, error_info)
            return self._error_response(request_id, COMMAND_FAILED, error_info["message"], error_info)
        except PAIRING_ERRORS as e:
            error_info = categorize_error(e)
            await self._emit_command_error(method, error_info)
            return self._error_response(request_id, PAIRING_FAILED, error_info["message"], error_info)
        except ValueError as e:
            return self._error_response(request_id, INVALID_PARAMS, str(e))
        except Exception as e:
            error_info = categorize_error(e)
            await self._emit_command_error(method, error_info)
            return self._error_response(request_id, INTERNAL_ERROR, str(e), error_info)

    def _success_response(self, request_id: Any, result: Any) -> Dict:
        return {"jsonrpc": "2.0", "id": request_id, "result": result}

    def _error_response(self, request_id: Any, code: int, message: str, data: Any = None) -> Dict:
        error = JsonRpcError(code, message, data)
        return {"jsonrpc": "2.0", "id": request_id, "error": error.to_dict()}

    # Handler implementations
    async def _handle_health(self, params: Dict) -> Dict:
        """Return comprehensive health status with metrics."""
        response = {
            "status": "ok",
            "version": "0.1.0",
            "uptime_seconds": round(time.time() - self._start_time, 1),
            "connected": self.connection_manager.is_connected if self.connection_manager else False,
        }
        
        # Add device info if connected
        if self.connection_manager and self.connection_manager.is_connected:
            device_info = self.connection_manager.get_device_info()
            if device_info:
                response["device"] = {
                    "name": device_info.get("name"),
                    "identifier": device_info.get("identifier"),
                }
        
        # Add memory and CPU metrics if psutil available
        if HAS_PSUTIL:
            try:
                process = psutil.Process(os.getpid())
                response["memory_mb"] = round(process.memory_info().rss / (1024 * 1024), 1)
                response["cpu_percent"] = round(process.cpu_percent(interval=0.1), 1)
            except Exception:
                response["memory_mb"] = 0.0
                response["cpu_percent"] = 0.0
        else:
            response["memory_mb"] = 0.0
            response["cpu_percent"] = 0.0
        
        return response

    async def _handle_scan(self, params: Dict) -> list:
        timeout = params.get("timeout", 5)
        devices = await self.connection_manager.scan_devices(timeout)
        return devices

    async def _handle_connect(self, params: Dict) -> Dict:
        identifier = params.get("identifier")
        if not identifier:
            raise ValueError("identifier is required")
        
        success = await self.connection_manager.connect(identifier)
        if success:
            device_info = self.connection_manager.get_device_info()
            await self.emit_event("connection-state", {"state": "Connected", "device": device_info})
            return {"success": True, "device": device_info}
        else:
            await self.emit_event("connection-state", {"state": "Failed"})
            raise Exception("Connection failed")

    async def _handle_disconnect(self, params: Dict) -> Dict:
        await self.connection_manager.disconnect()
        await self.emit_event("connection-state", {"state": "Disconnected"})
        return {"success": True}

    async def _handle_remote_command(self, params: Dict) -> Dict:
        command = params.get("command")
        action = params.get("action", "single_tap")
        
        if not command:
            raise ValueError("command is required")
        
        success = await self.connection_manager.send_command(command, action)
        return {"success": success}

    async def _handle_start_pairing(self, params: Dict) -> Dict:
        identifier = params.get("identifier")
        protocol = params.get("protocol", "companion")
        
        if not identifier:
            raise ValueError("identifier is required")
        
        result = await self.connection_manager.start_pairing(identifier, protocol)
        return result

    async def _handle_finish_pairing(self, params: Dict) -> Dict:
        pin = params.get("pin")
        if not pin:
            raise ValueError("pin is required")
        
        success = await self.connection_manager.finish_pairing(pin)
        return {"success": success}

    async def _handle_get_status(self, params: Dict) -> Dict:
        return {
            "connected": self.connection_manager.is_connected,
            "reconnecting": self.connection_manager.is_reconnecting,
            "device": self.connection_manager.get_device_info(),
            "playback": self.connection_manager.get_playback_state(),
        }

    async def _handle_list_saved_devices(self, params: Dict) -> list:
        """Return all devices with stored credentials."""
        devices = await self.connection_manager.list_saved_devices()
        return devices

    async def _handle_forget_device(self, params: Dict) -> Dict:
        """Remove stored credentials for a device."""
        identifier = params.get("identifier")
        if not identifier:
            raise ValueError("identifier is required")
        
        success = await self.connection_manager.forget_device(identifier)
        return {"success": success}

    async def _handle_set_text(self, params: Dict) -> Dict:
        """Set text in Apple TV virtual keyboard."""
        text = params.get("text", "")
        
        if not self.connection_manager.is_connected:
            raise Exception("Not connected to Apple TV")
        
        success = await self.connection_manager.set_text(text)
        return {"success": success, "text": text}

    async def _handle_clear_text(self, params: Dict) -> Dict:
        """Clear text in Apple TV virtual keyboard."""
        if not self.connection_manager.is_connected:
            raise Exception("Not connected to Apple TV")
        
        success = await self.connection_manager.clear_text()
        return {"success": success}

    async def _handle_get_text(self, params: Dict) -> Dict:
        """Get current text from Apple TV virtual keyboard."""
        if not self.connection_manager.is_connected:
            raise Exception("Not connected to Apple TV")
        
        text = await self.connection_manager.get_text()
        return {"success": True, "text": text}

    async def _handle_cancel_reconnect(self, params: Dict) -> Dict:
        """Cancel any pending reconnection attempts."""
        cancelled = self.connection_manager.cancel_reconnect()
        if cancelled:
            await self.emit_event("connection-state", {"state": "Disconnected"})
        return {"success": True, "was_reconnecting": cancelled}

    async def _handle_system_wake(self, params: Dict) -> Dict:
        """Handle system wake event - trigger immediate reconnection.
        
        Called when the Rust side detects a system wake via heartbeat gap.
        Skips normal backoff delays and attempts to reconnect immediately.
        """
        gap_seconds = params.get("gap_seconds", 0)
        _log(f"System wake detected (gap: {gap_seconds}s) - triggering reconnect")
        
        result = await self.connection_manager.trigger_wake_reconnect()
        return result


def _log(msg: str) -> None:
    """Log to stderr for debugging."""
    print(f"[server] {msg}", file=sys.stderr)
