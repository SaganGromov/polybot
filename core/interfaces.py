from abc import ABC, abstractmethod
from typing import List
from .models import Position, Order, MarketDepth, MarketMetadata

class ExchangeProvider(ABC):
    
    @abstractmethod
    async def get_balance(self) -> float:
        """Returns the current available USDC balance."""
        pass

    @abstractmethod
    async def get_positions(self, min_value: float = 0.0) -> List[Position]:
        """Returns a list of currently open positions with value >= min_value."""
        pass

    @abstractmethod
    async def place_order(self, order: Order) -> str:
        """
        Places an order on the exchange.
        
        Args:
            order: The generic Order model to place.
            
        Returns:
            str: The ID of the placed order.
        """
        pass

    @abstractmethod
    async def get_order_book(self, token_id: str) -> MarketDepth:
        """Returns the current market depth (bids/asks) for a token."""
        pass

    @abstractmethod
    async def get_market_metadata(self, token_id: str) -> 'MarketMetadata': # Forward ref or use string
        """Returns human-readable metadata for a market."""
        pass

    async def start(self):
        """Optional lifecycle hook to start background tasks (e.g. websockets)."""
        pass

    async def stop(self):
        """Optional lifecycle hook to stop background tasks."""
        pass
