"""
Trade Logger Service

Logs every real trade (buy/sell) to a JSON file with full context including:
- Trade details (token, side, size, price)
- Whale information (name, address, trade size)
- AI analysis (justification, confidence, risks, opportunities)
- Market metadata (question, category, volume, outcomes)
- Trigger reason (mirror whale, stop loss, take profit)
"""

import json
import os
import logging
from datetime import datetime
from typing import Optional, Dict, Any, List
from pydantic import BaseModel

logger = logging.getLogger(__name__)

# Default log file path
TRADE_LOG_FILE = "polybot/logs/trades.json"


class TradeLogEntry(BaseModel):
    """Structured trade log entry."""
    # Core trade info
    timestamp: str
    trade_type: str  # "BUY" or "SELL"
    trigger_reason: str  # "whale_mirror", "stop_loss", "take_profit"
    
    # Token/Market info
    token_id: str
    market_label: str
    market_question: Optional[str] = None
    market_category: Optional[str] = None
    market_status: Optional[str] = None
    market_volume: Optional[float] = None
    market_end_date: Optional[str] = None
    market_outcomes: Optional[Dict[str, float]] = None
    
    # Trade execution details
    size: float
    price: float
    cost_usd: Optional[float] = None
    
    # Position info (for sells)
    entry_price: Optional[float] = None
    roi_percent: Optional[float] = None
    
    # Whale info (for buys)
    whale_name: Optional[str] = None
    whale_address: Optional[str] = None
    whale_trade_size: Optional[float] = None
    whale_outcome: Optional[str] = None
    
    # AI analysis (if available)
    ai_enabled: bool = False
    ai_should_trade: Optional[bool] = None
    ai_confidence: Optional[float] = None
    ai_justification: Optional[str] = None
    ai_risk_factors: Optional[List[str]] = None
    ai_opportunity_factors: Optional[List[str]] = None
    ai_estimated_resolution: Optional[str] = None
    ai_subjectivity_score: Optional[float] = None
    ai_from_cache: bool = False
    ai_manual_override: bool = False
    
    # Strategy parameters at time of trade
    stop_loss_pct: Optional[float] = None
    take_profit_pct: Optional[float] = None
    min_share_price: Optional[float] = None
    max_budget: Optional[float] = None
    cumulative_spend: Optional[float] = None


class TradeLogger:
    """
    Logs all real trades to a JSON file for analysis and record-keeping.
    """
    
    def __init__(self, log_file: str = TRADE_LOG_FILE):
        self.log_file = log_file
        self._ensure_log_file_exists()
        logger.info(f"📝 TradeLogger initialized: {self.log_file}")
    
    def _ensure_log_file_exists(self):
        """Create log file and directory if they don't exist."""
        os.makedirs(os.path.dirname(self.log_file), exist_ok=True)
        if not os.path.exists(self.log_file):
            with open(self.log_file, 'w') as f:
                json.dump([], f)
    
    def _load_logs(self) -> List[Dict[str, Any]]:
        """Load existing trade logs."""
        try:
            with open(self.log_file, 'r') as f:
                return json.load(f)
        except (json.JSONDecodeError, FileNotFoundError):
            return []
    
    def _save_logs(self, logs: List[Dict[str, Any]]):
        """Save trade logs to file."""
        try:
            with open(self.log_file, 'w') as f:
                json.dump(logs, f, indent=2, default=str)
        except Exception as e:
            logger.error(f"Failed to save trade log: {e}")
    
    def log_trade(self, entry: TradeLogEntry):
        """Add a trade log entry."""
        logs = self._load_logs()
        logs.append(entry.model_dump())
        self._save_logs(logs)
        
        # Log summary to console
        emoji = "🟢" if entry.trade_type == "BUY" else "🔴"
        logger.info(f"📝 Trade Logged: {emoji} {entry.trade_type} | {entry.trigger_reason} | {entry.size:.2f} @ ${entry.price:.3f} | {entry.market_label}")
    
    def log_buy(
        self,
        token_id: str,
        market_label: str,
        size: float,
        price: float,
        cost_usd: float,
        whale_name: str,
        whale_address: str,
        whale_trade_size: float,
        whale_outcome: str,
        market_metadata: Optional[Any] = None,
        ai_analysis: Optional[Any] = None,
        ai_enabled: bool = False,
        ai_from_cache: bool = False,
        ai_manual_override: bool = False,
        strategy_params: Optional[Dict[str, Any]] = None
    ):
        """Log a BUY trade with all context."""
        entry = TradeLogEntry(
            timestamp=datetime.utcnow().isoformat(),
            trade_type="BUY",
            trigger_reason="whale_mirror",
            token_id=token_id,
            market_label=market_label,
            size=size,
            price=price,
            cost_usd=cost_usd,
            whale_name=whale_name,
            whale_address=whale_address,
            whale_trade_size=whale_trade_size,
            whale_outcome=whale_outcome,
            ai_enabled=ai_enabled,
            ai_from_cache=ai_from_cache,
            ai_manual_override=ai_manual_override,
        )
        
        # Add market metadata if available
        if market_metadata:
            entry.market_question = getattr(market_metadata, 'question', None)
            entry.market_category = getattr(market_metadata, 'category', None)
            entry.market_status = getattr(market_metadata, 'status', None)
            entry.market_volume = getattr(market_metadata, 'volume', None)
            entry.market_end_date = getattr(market_metadata, 'end_date', None)
            entry.market_outcomes = getattr(market_metadata, 'outcomes', None)
        
        # Add AI analysis if available
        if ai_analysis:
            entry.ai_should_trade = getattr(ai_analysis, 'should_trade', None)
            entry.ai_confidence = getattr(ai_analysis, 'confidence', None)
            entry.ai_justification = getattr(ai_analysis, 'justification', None)
            entry.ai_risk_factors = getattr(ai_analysis, 'risk_factors', None)
            entry.ai_opportunity_factors = getattr(ai_analysis, 'opportunity_factors', None)
            entry.ai_estimated_resolution = getattr(ai_analysis, 'estimated_resolution_time', None)
            entry.ai_subjectivity_score = getattr(ai_analysis, 'subjectivity_score', None)
        
        # Add strategy parameters if available
        if strategy_params:
            entry.stop_loss_pct = strategy_params.get('stop_loss_pct')
            entry.take_profit_pct = strategy_params.get('take_profit_pct')
            entry.min_share_price = strategy_params.get('min_share_price')
            entry.max_budget = strategy_params.get('max_budget')
            entry.cumulative_spend = strategy_params.get('cumulative_spend')
        
        self.log_trade(entry)
    
    def log_sell(
        self,
        token_id: str,
        market_label: str,
        trigger_reason: str,  # "stop_loss" or "take_profit"
        size: float,
        price: float,
        entry_price: float,
        roi_percent: float,
        market_metadata: Optional[Any] = None,
        strategy_params: Optional[Dict[str, Any]] = None
    ):
        """Log a SELL trade with all context."""
        entry = TradeLogEntry(
            timestamp=datetime.utcnow().isoformat(),
            trade_type="SELL",
            trigger_reason=trigger_reason,
            token_id=token_id,
            market_label=market_label,
            size=size,
            price=price,
            entry_price=entry_price,
            roi_percent=roi_percent,
        )
        
        # Add market metadata if available
        if market_metadata:
            entry.market_question = getattr(market_metadata, 'question', None)
            entry.market_category = getattr(market_metadata, 'category', None)
            entry.market_status = getattr(market_metadata, 'status', None)
            entry.market_volume = getattr(market_metadata, 'volume', None)
            entry.market_end_date = getattr(market_metadata, 'end_date', None)
            entry.market_outcomes = getattr(market_metadata, 'outcomes', None)
        
        # Add strategy parameters if available
        if strategy_params:
            entry.stop_loss_pct = strategy_params.get('stop_loss_pct')
            entry.take_profit_pct = strategy_params.get('take_profit_pct')
            entry.min_share_price = strategy_params.get('min_share_price')
            entry.max_budget = strategy_params.get('max_budget')
            entry.cumulative_spend = strategy_params.get('cumulative_spend')
        
        self.log_trade(entry)
    
    def get_all_trades(self) -> List[Dict[str, Any]]:
        """Get all logged trades."""
        return self._load_logs()
    
    def get_summary(self) -> Dict[str, Any]:
        """Get a summary of all trades."""
        logs = self._load_logs()
        
        buys = [t for t in logs if t.get('trade_type') == 'BUY']
        sells = [t for t in logs if t.get('trade_type') == 'SELL']
        stop_losses = [t for t in sells if t.get('trigger_reason') == 'stop_loss']
        take_profits = [t for t in sells if t.get('trigger_reason') == 'take_profit']
        
        return {
            "total_trades": len(logs),
            "total_buys": len(buys),
            "total_sells": len(sells),
            "stop_losses": len(stop_losses),
            "take_profits": len(take_profits),
            "total_buy_volume": sum(t.get('cost_usd', 0) or 0 for t in buys),
        }
