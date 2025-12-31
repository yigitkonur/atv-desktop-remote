"""Exponential backoff implementation for connection reliability.

Provides intelligent retry timing to prevent thundering herd,
resource exhaustion, and battery drain during reconnection attempts.
"""

import random
from dataclasses import dataclass
from typing import Optional


@dataclass
class BackoffConfig:
    """Configuration for exponential backoff behavior."""
    
    base_delay: float = 1.0      # Initial delay in seconds
    max_delay: float = 60.0      # Maximum delay cap in seconds
    max_attempts: int = 10       # Maximum retry attempts
    jitter_factor: float = 0.5   # Jitter factor (0.5 = 50%)
    
    @classmethod
    def aggressive(cls) -> "BackoffConfig":
        """Quick retries for local network."""
        return cls(
            base_delay=0.5,
            max_delay=30.0,
            max_attempts=15,
            jitter_factor=0.3,
        )
    
    @classmethod
    def conservative(cls) -> "BackoffConfig":
        """Slow retries for battery conservation."""
        return cls(
            base_delay=2.0,
            max_delay=120.0,
            max_attempts=5,
            jitter_factor=0.5,
        )


class ExponentialBackoff:
    """Exponential backoff state machine for retry logic.
    
    Usage:
        backoff = ExponentialBackoff()
        
        while (delay := backoff.next_delay()) is not None:
            await asyncio.sleep(delay)
            try:
                await connect()
                backoff.reset()
                break
            except ConnectionError:
                continue
        else:
            # Max attempts exhausted
            raise ConnectionFailed()
    """
    
    def __init__(self, config: Optional[BackoffConfig] = None):
        self.config = config or BackoffConfig()
        self._attempt: int = 0
    
    def next_delay(self) -> Optional[float]:
        """Get the next delay, incrementing attempt counter.
        
        Returns:
            Delay in seconds, or None if max attempts exhausted.
        """
        if self._attempt >= self.config.max_attempts:
            return None
        
        self._attempt += 1
        return self._calculate_delay()
    
    def peek_delay(self) -> float:
        """Preview the next delay without incrementing counter."""
        return self._calculate_delay()
    
    def _calculate_delay(self) -> float:
        """Calculate delay for current attempt.
        
        Formula: delay = min(base * 2^(attempt-1), max) + jitter
        """
        exponent = max(0, self._attempt - 1)
        exponential = self.config.base_delay * (2 ** exponent)
        capped = min(exponential, self.config.max_delay)
        
        # Add jitter to prevent thundering herd
        jitter_range = capped * self.config.jitter_factor
        jitter = random.uniform(0, jitter_range)
        
        return capped + jitter
    
    def reset(self) -> None:
        """Reset backoff state after successful connection."""
        self._attempt = 0
    
    @property
    def attempts(self) -> int:
        """Current attempt number (1-indexed after first next_delay call)."""
        return self._attempt
    
    @property
    def exhausted(self) -> bool:
        """Check if max attempts have been exhausted."""
        return self._attempt >= self.config.max_attempts
    
    @property
    def max_attempts(self) -> int:
        """Get maximum attempts configured."""
        return self.config.max_attempts
