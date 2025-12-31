"""Connection manager for pyatv Apple TV connections."""

import asyncio
import os
import sys
from pathlib import Path
from typing import Any, Callable, Coroutine, Dict, List, Optional

import pyatv
from pyatv.const import Protocol, InputAction
from pyatv.interface import AppleTV
from pyatv.storage.file_storage import FileStorage
from listeners import CombinedListener
from backoff import ExponentialBackoff, BackoffConfig
from errors import categorize_error, is_retryable, RETRYABLE_ERRORS, NON_RETRYABLE_ERRORS

def _get_storage_path() -> Path:
    """Get cross-platform storage path for credentials."""
    if sys.platform == "darwin":
        storage_dir = Path.home() / ".config" / "apple-tv-remote"
    elif sys.platform == "win32":
        storage_dir = Path(os.environ.get("APPDATA", "")) / "apple-tv-remote"
    else:
        storage_dir = Path.home() / ".config" / "apple-tv-remote"
    
    storage_dir.mkdir(parents=True, exist_ok=True)
    return storage_dir / "credentials.json"


def _log(msg: str) -> None:
    """Log to stderr for debugging."""
    print(f"[connection] {msg}", file=sys.stderr)


class ConnectionManager:
    """Manages Apple TV device discovery, connection, and commands."""

    def __init__(self, emit_callback: Optional[Callable[[str, Any], Coroutine]] = None):
        self._atv: Optional[AppleTV] = None
        self._config: Optional[pyatv.interface.BaseConfig] = None
        self._pairing: Optional[Any] = None
        self._scanned_devices: Dict[str, pyatv.interface.BaseConfig] = {}
        self._emit_callback = emit_callback
        self._listeners: Optional[CombinedListener] = None
        self._storage: Optional[FileStorage] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        # Reconnection state
        self._reconnect_task: Optional[asyncio.Task] = None
        self._last_identifier: Optional[str] = None
        self._is_reconnecting: bool = False

    async def initialize(self) -> None:
        """Initialize storage. Must be called before other operations."""
        self._loop = asyncio.get_running_loop()
        storage_path = _get_storage_path()
        self._storage = FileStorage(str(storage_path), self._loop)
        
        if storage_path.exists():
            try:
                await self._storage.load()
            except pyatv.exceptions.SettingsError as e:
                error_info = categorize_error(e)
                print(f"[{error_info['category']}] Failed to load credentials: {error_info['message']}", file=sys.stderr)
            except Exception as e:
                print(f"[unknown] Failed to load credentials: {e}", file=sys.stderr)

    @property
    def is_connected(self) -> bool:
        return self._atv is not None

    def get_device_info(self) -> Optional[Dict]:
        if not self._config:
            return None
        return {
            "identifier": self._config.identifier,
            "name": self._config.name,
            "address": str(self._config.address),
        }

    def get_playback_state(self) -> Optional[Dict]:
        if not self._atv:
            return None
        try:
            playing = self._atv.metadata.playing
            return {
                "state": str(playing.device_state),
                "title": playing.title,
                "artist": playing.artist,
                "album": playing.album,
                "position": playing.position,
                "total_time": playing.total_time,
            }
        except Exception:
            return None

    async def scan_devices(self, timeout: int = 5) -> List[Dict]:
        """Scan for Apple TV devices on the network."""
        devices = await pyatv.scan(
            asyncio.get_running_loop(),
            timeout=timeout,
            storage=self._storage
        )
        
        self._scanned_devices = {}
        result = []
        
        for device in devices:
            self._scanned_devices[device.identifier] = device
            
            # Check if we have stored credentials for this device
            paired = False
            if self._storage:
                try:
                    settings = await self._storage.get_settings(device)
                    paired = bool(
                        settings.protocols.companion.credentials or
                        settings.protocols.airplay.credentials or
                        settings.protocols.mrp.credentials
                    )
                except Exception:
                    pass
            
            result.append({
                "identifier": device.identifier,
                "name": device.name,
                "address": str(device.address),
                "services": [str(s.protocol) for s in device.services],
                "paired": paired,
            })
        
        return result

    async def connect(self, identifier: str) -> bool:
        """Connect to an Apple TV device by identifier."""
        if identifier not in self._scanned_devices:
            # Try to scan again
            await self.scan_devices()
            if identifier not in self._scanned_devices:
                return False

        config = self._scanned_devices[identifier]
        
        try:
            self._atv = await pyatv.connect(
                config,
                asyncio.get_running_loop(),
                storage=self._storage
            )
            self._config = config
            
            # Attach listeners for real-time updates
            if self._emit_callback:
                device_info = self.get_device_info()
                self._listeners = CombinedListener(
                    self._emit_callback,
                    device_info,
                    on_connection_lost=self._handle_connection_lost,
                )
                self._listeners.attach(self._atv)
                _log("Listeners attached successfully")
            
            return True
        except RETRYABLE_ERRORS as e:
            error_info = categorize_error(e)
            print(f"[{error_info['category']}] {error_info['type']}: {error_info['message']}", file=sys.stderr)
            return False
        except NON_RETRYABLE_ERRORS as e:
            error_info = categorize_error(e)
            print(f"[{error_info['category']}] {error_info['type']}: {error_info['message']}", file=sys.stderr)
            raise  # Re-raise for caller to handle (e.g., prompt re-pairing)
        except Exception as e:
            print(f"[unknown] Connection error: {e}", file=sys.stderr)
            return False

    async def _handle_connection_lost(self) -> None:
        """Handle unexpected connection loss - triggers automatic reconnection."""
        _log("Handling connection lost - initiating reconnection")
        
        # Store device identifier for reconnection before clearing state
        last_identifier = self._config.identifier if self._config else self._last_identifier
        last_device_info = self.get_device_info()
        
        # Clean up state but don't try to close (already lost)
        self._listeners = None
        self._atv = None
        # Keep _config for device info during reconnection
        
        # Schedule reconnection if we have a device to reconnect to
        if last_identifier:
            self._last_identifier = last_identifier
            await self._schedule_reconnect(last_device_info)
    
    async def _schedule_reconnect(self, device_info: Optional[Dict] = None) -> None:
        """Schedule a reconnection attempt with exponential backoff."""
        # Cancel any existing reconnection task
        if self._reconnect_task and not self._reconnect_task.done():
            self._reconnect_task.cancel()
            try:
                await self._reconnect_task
            except asyncio.CancelledError:
                pass
        
        self._is_reconnecting = True
        self._reconnect_task = asyncio.create_task(
            self._reconnect_loop(device_info)
        )
    
    async def _reconnect_loop(self, device_info: Optional[Dict] = None) -> None:
        """Reconnection loop with exponential backoff.
        
        Attempts to reconnect with delays: 1s → 2s → 4s → 8s → 16s → 32s → 60s (cap)
        Max 10 attempts before giving up.
        """
        backoff = ExponentialBackoff(BackoffConfig())
        last_error: Optional[str] = None
        
        _log(f"Starting reconnection loop for device: {self._last_identifier}")
        
        try:
            while True:
                # Get next delay (None if exhausted)
                delay = backoff.next_delay()
                
                if delay is None:
                    # Max attempts reached
                    _log(f"Reconnection failed after {backoff.max_attempts} attempts")
                    self._is_reconnecting = False
                    
                    if self._emit_callback:
                        await self._emit_callback("connection-state", {
                            "state": "Failed",
                            "device": device_info,
                            "error": last_error or "Max reconnection attempts reached",
                            "attempt": backoff.attempts,
                            "max_attempts": backoff.max_attempts,
                        })
                    return
                
                # Emit reconnecting status with countdown info
                delay_seconds = int(delay)
                _log(f"Reconnect attempt {backoff.attempts}/{backoff.max_attempts} in {delay_seconds}s")
                
                if self._emit_callback:
                    await self._emit_callback("connection-state", {
                        "state": "Reconnecting",
                        "device": device_info,
                        "attempt": backoff.attempts,
                        "max_attempts": backoff.max_attempts,
                        "next_retry_in": delay_seconds,
                        "error": last_error,
                    })
                
                # Wait for the delay
                await asyncio.sleep(delay)
                
                # Check if we should still be reconnecting
                if not self._is_reconnecting:
                    _log("Reconnection cancelled")
                    return
                
                # Re-scan to find device (IP may have changed)
                _log("Scanning for device...")
                try:
                    await self.scan_devices(timeout=5)
                except Exception as e:
                    _log(f"Scan failed: {e}")
                    last_error = str(e)
                    continue
                
                # Attempt connection
                if self._last_identifier and self._last_identifier in self._scanned_devices:
                    _log(f"Attempting to connect to {self._last_identifier}")
                    try:
                        success = await self.connect(self._last_identifier)
                        if success:
                            # Connection successful!
                            _log("Reconnection successful!")
                            self._is_reconnecting = False
                            backoff.reset()
                            
                            if self._emit_callback:
                                await self._emit_callback("connection-state", {
                                    "state": "Connected",
                                    "device": self.get_device_info(),
                                })
                            return
                        else:
                            last_error = "Connection attempt failed"
                    except NON_RETRYABLE_ERRORS as e:
                        # Auth errors, etc. - stop retrying
                        error_info = categorize_error(e)
                        _log(f"Non-retryable error: {error_info['message']}")
                        self._is_reconnecting = False
                        
                        if self._emit_callback:
                            await self._emit_callback("connection-state", {
                                "state": "Failed",
                                "device": device_info,
                                "error": error_info['message'],
                                "requires_repairing": error_info['category'] == 'authentication',
                            })
                        return
                    except Exception as e:
                        last_error = str(e)
                        _log(f"Connection attempt failed: {e}")
                else:
                    last_error = "Device not found on network"
                    _log(f"Device {self._last_identifier} not found in scan results")
        
        except asyncio.CancelledError:
            _log("Reconnection loop cancelled")
            self._is_reconnecting = False
            raise
    
    def cancel_reconnect(self) -> bool:
        """Cancel any pending reconnection attempts.
        
        Returns:
            True if a reconnection was cancelled, False if none was in progress.
        """
        if self._reconnect_task and not self._reconnect_task.done():
            self._reconnect_task.cancel()
            self._is_reconnecting = False
            _log("Reconnection cancelled by user")
            return True
        return False
    
    async def trigger_wake_reconnect(self) -> Dict:
        """Trigger immediate reconnection after system wake.
        
        Skips normal backoff delay and attempts to reconnect immediately.
        Called when system wake is detected via heartbeat gap.
        
        Returns:
            Dict with success status and message.
        """
        _log("Wake reconnect triggered - attempting immediate reconnection")
        
        # Cancel any existing reconnection (will be replaced with immediate attempt)
        self.cancel_reconnect()
        
        # Check if we have a device to reconnect to
        if not self._last_identifier:
            _log("No device identifier stored - cannot reconnect after wake")
            return {"success": False, "message": "No device to reconnect to"}
        
        # If already connected, just verify connection
        if self.is_connected:
            _log("Already connected - wake reconnect not needed")
            return {"success": True, "message": "Already connected"}
        
        # Emit wake-specific reconnecting state
        if self._emit_callback:
            await self._emit_callback("connection-state", {
                "state": "Reconnecting",
                "device": self.get_device_info(),
                "attempt": 1,
                "max_attempts": 3,
                "next_retry_in": 0,
                "error": None,
                "wake_recovery": True,
            })
        
        # Scan for device (IP may have changed after wake)
        _log("Scanning for device after wake...")
        try:
            await self.scan_devices(timeout=5)
        except Exception as e:
            _log(f"Wake scan failed: {e}")
            return {"success": False, "message": f"Device scan failed: {e}"}
        
        # Attempt immediate connection
        if self._last_identifier in self._scanned_devices:
            _log(f"Device found, attempting immediate connection to {self._last_identifier}")
            try:
                success = await self.connect(self._last_identifier)
                if success:
                    _log("Wake reconnection successful!")
                    if self._emit_callback:
                        await self._emit_callback("connection-state", {
                            "state": "Connected",
                            "device": self.get_device_info(),
                        })
                    return {"success": True, "message": "Reconnected after wake"}
                else:
                    _log("Wake reconnection attempt failed")
                    # Fall back to normal reconnection loop
                    await self._schedule_reconnect(self.get_device_info())
                    return {"success": False, "message": "Connection failed, starting reconnection"}
            except Exception as e:
                _log(f"Wake reconnection error: {e}")
                await self._schedule_reconnect(self.get_device_info())
                return {"success": False, "message": str(e)}
        else:
            _log(f"Device {self._last_identifier} not found after wake scan")
            # Start normal reconnection loop
            await self._schedule_reconnect(self.get_device_info())
            return {"success": False, "message": "Device not found, starting reconnection"}
    
    @property
    def is_reconnecting(self) -> bool:
        """Check if reconnection is in progress."""
        return self._is_reconnecting

    async def disconnect(self):
        """Disconnect from the current Apple TV."""
        # Cancel any pending reconnection first
        self.cancel_reconnect()
        
        if self._atv:
            # Detach listeners BEFORE closing connection
            if self._listeners:
                self._listeners.detach(self._atv)
                self._listeners = None
                _log("Listeners detached")
            
            self._atv.close()
            self._atv = None
            self._config = None

    async def send_command(self, command: str, action: str = "single_tap") -> bool:
        """Send a remote control command."""
        if not self._atv:
            return False

        rc = self._atv.remote_control
        
        # Map action string to InputAction
        input_action = InputAction.SingleTap
        if action == "double_tap":
            input_action = InputAction.DoubleTap
        elif action == "hold":
            input_action = InputAction.Hold

        # Command mapping
        command_map = {
            "up": lambda: rc.up(input_action),
            "down": lambda: rc.down(input_action),
            "left": lambda: rc.left(input_action),
            "right": lambda: rc.right(input_action),
            "select": lambda: rc.select(input_action),
            "menu": lambda: rc.menu(input_action),
            "home": lambda: rc.home(input_action),
            "home_hold": lambda: rc.home_hold(),
            "top_menu": lambda: rc.top_menu(),
            "play": lambda: rc.play(),
            "pause": lambda: rc.pause(),
            "play_pause": lambda: rc.play_pause(),
            "stop": lambda: rc.stop(),
            "next": lambda: rc.next(),
            "previous": lambda: rc.previous(),
            "skip_forward": lambda: rc.skip_forward(),
            "skip_backward": lambda: rc.skip_backward(),
            "volume_up": lambda: self._volume_up(),
            "volume_down": lambda: self._volume_down(),
        }

        handler = command_map.get(command)
        if not handler:
            return False

        try:
            await handler()
            return True
        except pyatv.exceptions.NotSupportedError as e:
            error_info = categorize_error(e)
            print(f"[{error_info['category']}] {error_info['type']}: {error_info['message']}", file=sys.stderr)
            raise  # Re-raise so caller knows feature is unsupported
        except pyatv.exceptions.ConnectionLostError as e:
            error_info = categorize_error(e)
            print(f"[{error_info['category']}] {error_info['type']}: {error_info['message']}", file=sys.stderr)
            raise  # Re-raise for reconnection handling
        except RETRYABLE_ERRORS as e:
            error_info = categorize_error(e)
            print(f"[{error_info['category']}] {error_info['type']}: {error_info['message']}", file=sys.stderr)
            return False
        except Exception as e:
            print(f"[unknown] Command error: {e}", file=sys.stderr)
            return False

    async def _volume_up(self):
        if self._atv and hasattr(self._atv, "audio"):
            await self._atv.audio.volume_up()

    async def _volume_down(self):
        if self._atv and hasattr(self._atv, "audio"):
            await self._atv.audio.volume_down()

    async def start_pairing(self, identifier: str, protocol: str = "companion") -> Dict:
        """Start pairing process with a device."""
        if identifier not in self._scanned_devices:
            await self.scan_devices()
            if identifier not in self._scanned_devices:
                raise ValueError(f"Device not found: {identifier}")

        config = self._scanned_devices[identifier]
        
        # Map protocol string to Protocol enum
        proto = Protocol.Companion if protocol == "companion" else Protocol.AirPlay

        try:
            self._pairing = await pyatv.pair(
                config,
                proto,
                asyncio.get_running_loop(),
                storage=self._storage
            )
            await self._pairing.begin()
            
            return {
                "success": True,
                "requires_pin": self._pairing.device_provides_pin,
                "protocol": protocol,
            }
        except pyatv.exceptions.PairingError as e:
            self._pairing = None
            error_info = categorize_error(e)
            print(f"[{error_info['category']}] {error_info['type']}: {error_info['message']}", file=sys.stderr)
            raise
        except pyatv.exceptions.BackOffError as e:
            self._pairing = None
            error_info = categorize_error(e)
            print(f"[{error_info['category']}] {error_info['type']}: {error_info['message']}", file=sys.stderr)
            raise
        except Exception as e:
            self._pairing = None
            print(f"[unknown] Pairing failed: {e}", file=sys.stderr)
            raise Exception(f"Pairing failed: {e}")

    async def finish_pairing(self, pin: str) -> bool:
        """Complete pairing with PIN."""
        if not self._pairing:
            return False

        try:
            self._pairing.pin(pin)
            await self._pairing.finish()
            
            # Save credentials to storage
            if self._pairing.has_paired and self._storage:
                await self._storage.save()
                _log("Paired successfully, credentials saved")
            
            await self._pairing.close()
            self._pairing = None
            return True
        except pyatv.exceptions.PairingError as e:
            error_info = categorize_error(e)
            print(f"[{error_info['category']}] {error_info['type']}: {error_info['message']}", file=sys.stderr)
            await self._pairing.close()
            self._pairing = None
            raise
        except pyatv.exceptions.BackOffError as e:
            error_info = categorize_error(e)
            print(f"[{error_info['category']}] {error_info['type']}: {error_info['message']}", file=sys.stderr)
            await self._pairing.close()
            self._pairing = None
            raise
        except Exception as e:
            print(f"[unknown] Pairing finish error: {e}", file=sys.stderr)
            await self._pairing.close()
            self._pairing = None
            return False

    async def set_text(self, text: str) -> bool:
        """Set text in Apple TV virtual keyboard."""
        if not self._atv:
            return False
        
        try:
            await self._atv.keyboard.text_set(text)
            return True
        except Exception as e:
            print(f"Set text error: {e}", file=sys.stderr)
            return False

    async def clear_text(self) -> bool:
        """Clear text in Apple TV virtual keyboard."""
        if not self._atv:
            return False
        
        try:
            await self._atv.keyboard.text_clear()
            return True
        except Exception as e:
            print(f"Clear text error: {e}", file=sys.stderr)
            return False

    async def get_text(self) -> str:
        """Get current text from Apple TV virtual keyboard."""
        if not self._atv:
            return ""
        
        try:
            text = await self._atv.keyboard.text_get()
            return text or ""
        except Exception as e:
            print(f"Get text error: {e}", file=sys.stderr)
            return ""

    async def list_saved_devices(self) -> List[Dict]:
        """List all devices with stored credentials."""
        if not self._storage:
            return []
        
        result = []
        for settings in self._storage.settings:
            device_info = {
                "identifier": None,
                "name": settings.info.name if settings.info.name else "Unknown",
                "protocols": [],
            }
            
            if settings.protocols.companion.credentials:
                device_info["protocols"].append("companion")
                if not device_info["identifier"]:
                    device_info["identifier"] = settings.protocols.companion.identifier
            
            if settings.protocols.airplay.credentials:
                device_info["protocols"].append("airplay")
                if not device_info["identifier"]:
                    device_info["identifier"] = settings.protocols.airplay.identifier
            
            if settings.protocols.mrp.credentials:
                device_info["protocols"].append("mrp")
                if not device_info["identifier"]:
                    device_info["identifier"] = settings.protocols.mrp.identifier
            
            if device_info["identifier"] and device_info["protocols"]:
                result.append(device_info)
        
        return result

    async def forget_device(self, identifier: str) -> bool:
        """Remove stored credentials for a device."""
        if not self._storage:
            return False
        
        try:
            for settings in list(self._storage.settings):
                matches = (
                    settings.protocols.companion.identifier == identifier or
                    settings.protocols.airplay.identifier == identifier or
                    settings.protocols.mrp.identifier == identifier
                )
                if matches:
                    await self._storage.remove_settings(settings)
                    await self._storage.save()
                    _log(f"Removed credentials for device: {identifier}")
                    return True
            
            _log(f"No credentials found for device: {identifier}")
            return False
        except Exception as e:
            _log(f"Error removing credentials: {e}")
            return False
