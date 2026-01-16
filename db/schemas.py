from typing import Optional
from datetime import datetime
from sqlmodel import SQLModel, Field
from decimal import Decimal
from polybot.core.models import Side, OrderStatus

class TradeHistory(SQLModel, table=True):
    __tablename__: str = "trade_history"
    
    id: Optional[int] = Field(default=None, primary_key=True)
    token_id: str = Field(index=True)
    side: str  # Storing as string to avoid Enum db issues, using Side enum in logic
    price: float
    size: float
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    order_id: str = Field(index=True)
    realized_pnl: Optional[float] = Field(default=None) # Only relevant for closing trades
    is_dry_run: bool = Field(default=False)
    
class ActivePosition(SQLModel, table=True):
    __tablename__: str = "active_positions"

    token_id: str = Field(primary_key=True)
    size: float
    average_entry_price: float
    updated_at: datetime = Field(default_factory=datetime.utcnow)
