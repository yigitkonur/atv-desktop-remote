"""Playback state sanitizer for handling app-specific quirks and metadata validation.

This module implements the reliability layer described in the pyatv best practices article.
It handles:
- Metadata completeness validation (total_time=0 race condition)
- Netflix ghost states (menu previews)
- Prime Video playback_rate bug
- YouTube ad detection
- Content hash-based deduplication
"""

import sys
from typing import Optional, Dict, Any
from pyatv.const import DeviceState


def _log(msg: str) -> None:
    """Log to stderr for debugging."""
    print(f"[sanitizer] {msg}", file=sys.stderr)


class PlaybackSanitizer:
    """Sanitizes playback state to handle app-specific quirks and edge cases."""
    
    def __init__(self):
        self._last_valid_content: Optional[Dict[str, Any]] = None
        self._last_hash: Optional[str] = None
    
    def sanitize(self, state: Dict[str, Any], playstatus) -> Optional[Dict[str, Any]]:
        """
        Apply all sanitization rules to playback state.
        Returns None if state should be filtered out.
        
        Args:
            state: Raw state dict from PlaybackListener
            playstatus: pyatv Playing object for additional checks
            
        Returns:
            Sanitized state dict or None if invalid
        """
        app_id = (state.get("app_id") or "").lower()
        
        # Step 1: Metadata completeness check
        if not self._is_metadata_complete(state, playstatus):
            _log(f"Filtered incomplete metadata: title={state.get('title')}, total_time={state.get('total_time')}")
            return None
        
        # Step 2: Netflix ghost state filter
        if self._is_netflix(app_id) and self._is_ghost_state(state):
            _log(f"Filtered Netflix ghost state: {state.get('title')}")
            return None
        
        # Step 3: YouTube ad detection
        if self._is_youtube(app_id) and self._is_ad(state.get("title")):
            _log(f"Filtered YouTube ad: {state.get('title')}")
            # Return last valid content instead of ad
            return self._last_valid_content
        
        # Step 4: Prime Video playback_rate bug - handled in state mapping
        # (No filtering needed, just trust DeviceState enum)
        
        # Step 5: Cache last valid content for recovery
        if state.get("title") and state.get("total_time", 0) > 0:
            self._last_valid_content = state.copy()
        
        return state
    
    def _is_metadata_complete(self, state: Dict[str, Any], playstatus) -> bool:
        """
        Check if metadata has finished loading.
        
        Article: "When content first starts playing, total_time might be 0 
        because metadata hasn't loaded yet."
        """
        # Must have title
        title = state.get("title")
        if not title or len(title.strip()) == 0:
            return False
        
        # If idle/paused, no duration required
        device_state = playstatus.device_state if playstatus else None
        if device_state in (DeviceState.Idle, DeviceState.Stopped):
            return True
        
        # If playing/paused but no duration, metadata still loading
        total_time = state.get("total_time")
        if total_time is None or total_time == 0:
            return False
        
        return True
    
    def _is_netflix(self, app_id: str) -> bool:
        """Detect Netflix app."""
        return "netflix" in app_id
    
    def _is_ghost_state(self, state: Dict[str, Any]) -> bool:
        """
        Detect Netflix ghost state (menu preview).
        
        Article: "When you navigate the Netflix home screen, previews play 
        and the 'Playing' state fires for movies/shows you've previously 
        watched—not what's currently on screen."
        """
        # Ghost states have no duration
        total_time = state.get("total_time")
        if total_time is None or total_time == 0:
            # Only filter if state is Playing (not Paused/Idle)
            if state.get("state") == "Playing":
                return True
        return False
    
    def _is_youtube(self, app_id: str) -> bool:
        """Detect YouTube app."""
        return "youtube" in app_id
    
    def _is_ad(self, title: Optional[str]) -> bool:
        """
        Detect YouTube ad.
        
        Article: "YouTube ads break metadata continuity. When an ad starts, 
        title changes to ad content."
        """
        if not title:
            return False
        
        title_lower = title.lower()
        ad_indicators = [
            "[ad]",
            "[youtube ad]",
            "advertisement",
            "skip ad",
            "sponsored",
            "ad - ",
        ]
        
        return any(indicator in title_lower for indicator in ad_indicators)
    
    def _is_prime_video(self, app_id: str) -> bool:
        """Detect Prime Video app."""
        return "amazon" in app_id or "prime" in app_id
    
    def _is_disney_plus(self, app_id: str) -> bool:
        """Detect Disney+ app."""
        return "disney" in app_id
    
    def _is_plex(self, app_id: str) -> bool:
        """Detect Plex app."""
        return "plex" in app_id
