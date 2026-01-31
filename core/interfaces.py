from abc import ABC, abstractmethod
from typing import List, Optional
from .models import Position, Order, MarketDepth, MarketMetadata, TradeAnalysis, SportsSelectivityResult

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


class AIAnalysisProvider(ABC):
    """Abstract interface for AI-powered trade analysis."""
    
    @abstractmethod
    async def analyze_trade(
        self, 
        token_id: str, 
        market_metadata: MarketMetadata,
        market_depth: MarketDepth,
        context: dict
    ) -> TradeAnalysis:
        """
        Analyze whether a trade should be executed.
        
        Args:
            token_id: The Polymarket token ID
            market_metadata: Market details (title, question, outcomes, volume, etc.)
            market_depth: Current order book state
            context: Additional context (whale info, portfolio state, etc.)
            
        Returns:
            TradeAnalysis with recommendation and justification
        """
        pass

    @abstractmethod
    async def is_crypto_price_market(
        self,
        market_metadata: MarketMetadata
    ) -> tuple[bool, str]:
        """
        Detect if a market is a crypto price prediction bet.
        
        Examples: "Will BTC hit $100K?", "ETH above $4000 by March?"
        
        Args:
            market_metadata: Market details with title, question, category
            
        Returns:
            (is_crypto, reason) - True if market is crypto price prediction
        """
        pass

    @abstractmethod
    async def evaluate_sports_selectivity(
        self,
        market_metadata: MarketMetadata,
        max_days_to_resolution: float,
        min_favorite_odds: float
    ) -> SportsSelectivityResult:
        """
        Evaluate if a sports market qualifies for selective trading.
        
        Args:
            market_metadata: Market details with title, question, outcomes, end_date
            max_days_to_resolution: Maximum allowed days until resolution
            min_favorite_odds: Minimum odds required for the favorite (0.0 to 1.0)
            
        Returns:
            SportsSelectivityResult with qualification decision and details
        """
        pass

