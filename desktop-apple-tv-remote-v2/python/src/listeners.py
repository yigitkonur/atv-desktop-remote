"""pyatv event listeners for real-time updates.

This module implements listener classes for pyatv that emit events
to the Tauri frontend via JSON-RPC notifications.
"""

import asyncio
import sys
from typing import Any, Callable, Coroutine, List, Optional
from pyatv.interface import (
    PushListener,
    DeviceListener,
    PowerListener,
    AudioListener,
    KeyboardListener as PyATVKeyboardListener,
    Playing,
    OutputDevice,
)
from pyatv.const import (
    DeviceState,
    MediaType,
    PowerState,
    KeyboardFocusState,
)
from sanitizer import PlaybackSanitizer


def _log(msg: str) -> None:
    """Log to stderr for debugging."""
    print(f"[listeners] {msg}", file=sys.stderr)


class PlaybackListener(PushListener):
    """Listener for playback/now-playing state changes.
    
    Emits 'playback-update' events with title, artist, album, state, etc.
    Caches last state to avoid duplicate emissions.
    """
    
    def __init__(self, emit_callback: Callable[[str, Any], Coroutine], atv=None):
        self._emit = emit_callback
        self._last_hash: Optional[str] = None
        self._last_state: Optional[dict] = None
        self._atv = atv
        self._sanitizer = PlaybackSanitizer()
    
    def playstatus_update(self, updater, playstatus: Playing) -> None:
        """Called when playback status changes."""
        # Get app info if available
        app_name = None
        app_id = None
        if self._atv and hasattr(self._atv, 'metadata'):
            try:
                app = self._atv.metadata.app
                if app:
                    app_name = app.name
                    app_id = app.identifier
            except Exception as e:
                _log(f"Failed to get app info: {e}")
        
        # Build state dict
        state = {
            "title": playstatus.title,
            "artist": playstatus.artist,
            "album": playstatus.album,
            "state": playstatus.device_state.name if playstatus.device_state else "Unknown",
            "media_type": playstatus.media_type.name if playstatus.media_type else "Unknown",
            "position": playstatus.position,
            "total_time": playstatus.total_time,
            "app_name": app_name,
            "app_id": app_id,
        }
        
        # Sanitize state (handles quirks, validates metadata)
        sanitized_state = self._sanitizer.sanitize(state, playstatus)
        if sanitized_state is None:
            # State was filtered out (incomplete metadata, ghost state, ad, etc.)
            return
        
        # Use hash to detect content changes, compare state for other changes
        current_hash = playstatus.hash
        
        # Only emit if something meaningful changed
        if current_hash != self._last_hash or sanitized_state != self._last_state:
            self._last_hash = current_hash
            self._last_state = sanitized_state.copy()
            
            _log(f"Playback update: {sanitized_state['title']} - {sanitized_state['state']}")
            asyncio.create_task(self._emit("playback-update", sanitized_state))
    
    def playstatus_error(self, updater, exception: Exception) -> None:
        """Called when push update fails."""
        _log(f"Playback listener error: {exception}")
        asyncio.create_task(self._emit("playback-error", {"error": str(exception)}))


class ConnectionStateListener(DeviceListener):
    """Listener for connection state changes.
    
    Emits 'connection-state' events when connection is lost or closed.
    Can trigger reconnection via callback.
    """
    
    def __init__(
        self, 
        emit_callback: Callable[[str, Any], Coroutine],
        device_info: Optional[dict] = None,
        on_connection_lost: Optional[Callable[[], Coroutine]] = None,
    ):
        self._emit = emit_callback
        self._device_info = device_info
        self._on_connection_lost = on_connection_lost
    
    def connection_lost(self, exception: Exception) -> None:
        """Called when connection is unexpectedly lost."""
        _log(f"Connection lost: {exception}")
        
        event_data = {
            "state": "Reconnecting",
            "device": self._device_info,
            "error": str(exception),
        }
        asyncio.create_task(self._emit("connection-state", event_data))
        
        # Trigger reconnection if callback provided
        if self._on_connection_lost:
            asyncio.create_task(self._on_connection_lost())
    
    def connection_closed(self) -> None:
        """Called when connection is intentionally closed."""
        _log("Connection closed normally")
        
        event_data = {
            "state": "Disconnected",
            "device": self._device_info,
        }
        asyncio.create_task(self._emit("connection-state", event_data))


class VolumeListener(AudioListener):
    """Listener for volume and audio output changes.
    
    Emits 'volume-update' events when volume level changes.
    """
    
    def __init__(self, emit_callback: Callable[[str, Any], Coroutine]):
        self._emit = emit_callback
        self._last_volume: Optional[float] = None
    
    def volume_update(self, old_level: float, new_level: float) -> None:
        """Called when volume changes."""
        # Avoid duplicate emissions for same level
        if new_level == self._last_volume:
            return
        
        self._last_volume = new_level
        _log(f"Volume update: {old_level:.0f}% -> {new_level:.0f}%")
        
        asyncio.create_task(self._emit("volume-update", {
            "old_level": old_level,
            "new_level": new_level,
        }))
    
    def outputdevices_update(
        self,
        old_devices: List[OutputDevice],
        new_devices: List[OutputDevice],
    ) -> None:
        """Called when output devices change (multi-room audio)."""
        _log(f"Output devices changed: {len(new_devices)} devices")
        
        devices_data = [
            {"name": d.name, "identifier": d.identifier}
            for d in new_devices
        ]
        asyncio.create_task(self._emit("output-devices-update", {
            "devices": devices_data,
        }))


class KeyboardFocusListener(PyATVKeyboardListener):
    """Listener for keyboard/text field focus changes.
    
    Emits 'keyboard-focus' events when a text field gains/loses focus.
    """
    
    def __init__(self, emit_callback: Callable[[str, Any], Coroutine]):
        self._emit = emit_callback
        self._focused: Optional[bool] = None
    
    def focusstate_update(
        self,
        old_state: KeyboardFocusState,
        new_state: KeyboardFocusState,
    ) -> None:
        """Called when keyboard focus changes."""
        focused = new_state == KeyboardFocusState.Focused
        
        # Avoid duplicate emissions
        if focused == self._focused:
            return
        
        self._focused = focused
        _log(f"Keyboard focus: {focused}")
        
        asyncio.create_task(self._emit("keyboard-focus", {
            "focused": focused,
        }))


class CombinedListener:
    """Wrapper that manages all listeners and attaches them to a connection.
    
    Usage:
        listeners = CombinedListener(emit_callback, device_info)
        listeners.attach(atv)  # After connect
        # ... use connection ...
        listeners.detach(atv)  # Before disconnect
    """
    
    def __init__(
        self,
        emit_callback: Callable[[str, Any], Coroutine],
        device_info: Optional[dict] = None,
        on_connection_lost: Optional[Callable[[], Coroutine]] = None,
    ):
        self._emit = emit_callback
        self._device_info = device_info
        self._atv = None
        
        # Create listener instances (playback will be updated in attach)
        self.playback = None
        self.connection = ConnectionStateListener(
            emit_callback, 
            device_info,
            on_connection_lost,
        )
        self.volume = VolumeListener(emit_callback)
        self.keyboard = KeyboardFocusListener(emit_callback)
        
        # Track push_updater state to prevent double-stop errors
        self._push_started = False
    
    def attach(self, atv) -> None:
        """Attach all listeners to an Apple TV connection.
        
        IMPORTANT: This method starts push_updater - the most common
        anti-pattern is forgetting to call push_updater.start().
        """
        _log("Attaching listeners to connection")
        
        # Store atv reference and create playback listener with it
        self._atv = atv
        self.playback = PlaybackListener(self._emit, atv)
        
        # Device listener for connection state
        atv.listener = self.connection
        
        # Push updater for playback updates
        atv.push_updater.listener = self.playback
        atv.push_updater.start()
        self._push_started = True
        _log("push_updater.start() called")
        
        # Audio listener for volume
        if hasattr(atv, 'audio') and atv.audio:
            atv.audio.listener = self.volume
            _log("Audio listener attached")
        
        # Keyboard listener for text field focus
        if hasattr(atv, 'keyboard') and atv.keyboard:
            atv.keyboard.listener = self.keyboard
            _log("Keyboard listener attached")
        
        _log("All listeners attached successfully")
    
    def detach(self, atv) -> None:
        """Detach listeners from Apple TV connection.
        
        IMPORTANT: Must be called BEFORE closing the connection.
        Stops push_updater first to avoid errors.
        """
        _log("Detaching listeners from connection")
        
        # Stop push updater first (must happen before close)
        if self._push_started:
            try:
                atv.push_updater.stop()
                _log("push_updater.stop() called")
            except Exception as e:
                _log(f"Warning: Error stopping push updater: {e}")
            self._push_started = False
        
        _log("Listeners detached")
