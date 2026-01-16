class ExchangeError(Exception):
    """
    Base exception for all exchange-related errors.
    
    This acts as a catch-all for any anticipated error arising from 
    interaction with the exchange (Mock or Real).
    """
    pass

class APIError(ExchangeError):
    """
    Raised when the external API returns an HTTP error (5xx, 4xx).
    
    This typically implies a network issue, a bad request, or downtime 
    on the Polymarket side.
    """
    pass

class AuthError(ExchangeError):
    """
    Raised when authentication fails.
    
    Likely causes: Invalid Private Key, Wrong Proxy Address, or 
    Permission Denied on the API.
    """
    pass

class InsufficientFundsError(ExchangeError):
    """
    Raised when the wallet lacks funds for a trade.
    
    This is checked against the internal tracking in MockExchange
    or the realized balance in PolymarketAdapter.
    """
    pass

class OrderError(ExchangeError):
    """
    Raised when an order placement or cancellation fails.
    
    Examples: Invalid size, market not found, or generic exchange rejection.
    """
    pass
