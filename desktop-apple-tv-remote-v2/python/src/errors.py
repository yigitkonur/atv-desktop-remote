"""Error categorization and recovery helpers for pyatv integration.

This module provides structured error handling by categorizing pyatv exceptions
into retryable, non-retryable, and pairing-related errors. Each category has
appropriate recovery strategies and user-friendly messages.
"""

from typing import Any, Dict, Optional, Type
from pyatv import exceptions


# Errors that should trigger automatic retry with exponential backoff
RETRYABLE_ERRORS = (
    exceptions.ConnectionFailedError,
    exceptions.ConnectionLostError,
    exceptions.ProtocolError,
    exceptions.OperationTimeoutError,
)

# Errors that require user intervention (no automatic retry)
NON_RETRYABLE_ERRORS = (
    exceptions.AuthenticationError,
    exceptions.InvalidCredentialsError,
    exceptions.NoCredentialsError,
    exceptions.NotSupportedError,
    exceptions.NoServiceError,
)

# Errors during pairing process
PAIRING_ERRORS = (
    exceptions.PairingError,
    exceptions.BackOffError,
)


# User-friendly messages for each error type
USER_MESSAGES: Dict[Type[Exception], str] = {
    exceptions.ConnectionFailedError: "Cannot reach Apple TV. Check that it's powered on and on the same network.",
    exceptions.ConnectionLostError: "Connection to Apple TV was lost. Reconnecting...",
    exceptions.ProtocolError: "Communication error with Apple TV. Retrying...",
    exceptions.OperationTimeoutError: "Apple TV not responding. It may be asleep or busy.",
    exceptions.AuthenticationError: "Authentication failed. Please re-pair your device.",
    exceptions.InvalidCredentialsError: "Stored credentials are invalid. Please re-pair your device.",
    exceptions.NoCredentialsError: "No pairing credentials found. Please pair your device first.",
    exceptions.NotSupportedError: "This feature is not supported on your Apple TV.",
    exceptions.NoServiceError: "No compatible service found on Apple TV. Try scanning again.",
    exceptions.PairingError: "Pairing failed. Please try again.",
    exceptions.BackOffError: "Too many attempts. Please wait before trying again.",
}


def categorize_error(error: Exception) -> Dict[str, Any]:
    """Categorize a pyatv exception and return structured error information.
    
    Args:
        error: The exception to categorize
        
    Returns:
        A dictionary with the following structure:
        {
            "category": "retryable|non_retryable|pairing|unknown",
            "type": "ExceptionClassName",
            "message": "Human readable message",
            "action_required": "automatic_retry|user_intervention|retry_pairing|none",
            "should_retry": true|false,
            "technical_message": "Original exception message for debugging"
        }
    """
    error_type = type(error).__name__
    technical_message = str(error)
    
    # Determine category and action
    if isinstance(error, RETRYABLE_ERRORS):
        category = "retryable"
        action_required = "automatic_retry"
        should_retry = True
    elif isinstance(error, NON_RETRYABLE_ERRORS):
        category = "non_retryable"
        action_required = "user_intervention"
        should_retry = False
    elif isinstance(error, PAIRING_ERRORS):
        category = "pairing"
        action_required = "retry_pairing"
        should_retry = False
    else:
        category = "unknown"
        action_required = "none"
        should_retry = False
    
    # Get user-friendly message
    user_message = USER_MESSAGES.get(
        type(error),
        f"An unexpected error occurred: {technical_message}"
    )
    
    return {
        "category": category,
        "type": error_type,
        "message": user_message,
        "action_required": action_required,
        "should_retry": should_retry,
        "technical_message": technical_message,
    }


def get_retry_delay(attempt: int, base_delay: float = 1.0, max_delay: float = 60.0) -> float:
    """Calculate exponential backoff delay for retry attempts.
    
    Args:
        attempt: The current attempt number (0-indexed)
        base_delay: Initial delay in seconds
        max_delay: Maximum delay cap in seconds
        
    Returns:
        The delay in seconds before the next retry
    """
    delay = base_delay * (2 ** attempt)
    return min(delay, max_delay)


def is_retryable(error: Exception) -> bool:
    """Check if an error should trigger automatic retry.
    
    Args:
        error: The exception to check
        
    Returns:
        True if the error is retryable, False otherwise
    """
    return isinstance(error, RETRYABLE_ERRORS)


def requires_repairing(error: Exception) -> bool:
    """Check if an error indicates credentials need to be refreshed.
    
    Args:
        error: The exception to check
        
    Returns:
        True if re-pairing is required, False otherwise
    """
    return isinstance(error, (
        exceptions.AuthenticationError,
        exceptions.InvalidCredentialsError,
        exceptions.NoCredentialsError,
    ))
