from decimal import Decimal
from enum import Enum
from typing import Optional
from pydantic import BaseModel, Field

class Side(str, Enum):
    BUY = "BUY"
    SELL = "SELL"

class OrderStatus(str, Enum):
    PENDING = "PENDING"
    FILLED = "FILLED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"

class StrategyType(str, Enum):
    MIRROR = "MIRROR"
    INVERSE = "INVERSE"

class Position(BaseModel):
    token_id: str
    size: float
    average_entry_price: float
    current_price: float
    
    @property
    def value(self) -> float:
        return self.size * self.current_price

class Order(BaseModel):
    token_id: str
    market_name: str | None = None
    side: Side
    size: float
    price_limit: float
    status: OrderStatus = OrderStatus.PENDING
    order_id: Optional[str] = None
    
class WalletTarget(BaseModel):
    address: str
    name: str = Field(default="Unknown")
    strategy_type: StrategyType = StrategyType.MIRROR
    max_copy_amount: Optional[float] = None

class MarketDepthLevel(BaseModel):
    price: float
    size: float

class MarketDepth(BaseModel):
    bids: list[MarketDepthLevel]
    asks: list[MarketDepthLevel]
    min_order_size: float = 0.0

class MarketMetadata(BaseModel):
    title: str
    question: str
    group_name: str | None = None
    category: str | None = None
    status: str | None = None  # "Active" or "Closed"
    volume: float | None = None
    end_date: str | None = None
    outcomes: dict[str, float] | None = None  # outcome_name -> price
    score: str | None = None  # For sports markets
