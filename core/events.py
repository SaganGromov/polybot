from pydantic import BaseModel
from datetime import datetime
from polybot.core.models import Side

class TradeEvent(BaseModel):
    source_wallet_name: str
    source_wallet_address: str
    token_id: str
    market_slug: str
    outcome: str
    side: Side
    usd_size: float
    timestamp: datetime
