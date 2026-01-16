class ExchangeError(Exception):
    """Base exception for all exchange-related errors."""
    pass

class APIError(ExchangeError):
    """Raised when the external API returns an error (5xx, 4xx)."""
    pass

class AuthError(ExchangeError):
    """Raised when authentication fails."""
    pass

class InsufficientFundsError(ExchangeError):
    """Raised when the wallet lacks funds for a trade."""
    pass

class OrderError(ExchangeError):
    """Raised when an order placement or cancellation fails."""
    pass
