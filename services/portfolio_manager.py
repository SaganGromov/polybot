import asyncio
import logging
import math
import json
import os
from typing import List, Set
from polybot.core.interfaces import ExchangeProvider, MarketMetadata
from polybot.core.models import Position, Side, Order, OrderStatus
from polybot.core.events import TradeEvent
from polybot.services.execution import SmartExecutor
from polybot.config.settings import settings

logger = logging.getLogger(__name__)

class PortfolioManager:
    def __init__(self, exchange: ExchangeProvider, executor: SmartExecutor, stop_loss_pct: float = 0.20, take_profit_pct: float = 0.9, min_share_price: float = 0.19, log_interval_minutes: int = 60, max_budget: float = 100.0, min_position_value: float = 0.03, blacklisted_token_ids: List[str] = None):
        self.exchange = exchange
        self.executor = executor
        self.stop_loss_pct = stop_loss_pct
        self.take_profit_pct = take_profit_pct
        self.min_share_price = min_share_price
        self.log_interval_minutes = log_interval_minutes
        self.max_budget = max_budget
        self.min_position_value = min_position_value
        self.blacklisted_token_ids = set(blacklisted_token_ids or [])
        self._running = False
        
        # Persistent Bot State
        self.state_file = "polybot/config/bot_state.json"
        self.cumulative_spend = 0.0
        self.managed_tokens: Set[str] = set()
        self._load_state()

    def _load_state(self):
        if os.path.exists(self.state_file):
            try:
                with open(self.state_file, 'r') as f:
                    data = json.load(f)
                    self.cumulative_spend = data.get("cumulative_spend", 0.0)
                    self.managed_tokens = set(data.get("managed_tokens", []))
                logger.info(f"💾 Loaded Bot State: Cumulative Spend=${self.cumulative_spend:.2f}, Managed Tokens={len(self.managed_tokens)}")
            except Exception as e:
                logger.error(f"Failed to load bot state: {e}")

    def _save_state(self):
        try:
            data = {
                "cumulative_spend": self.cumulative_spend,
                "managed_tokens": list(self.managed_tokens)
            }
            os.makedirs(os.path.dirname(self.state_file), exist_ok=True)
            with open(self.state_file, 'w') as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            logger.error(f"Failed to save bot state: {e}")

    def update_strategies(self, stop_loss: float, take_profit: float, min_price: float, log_interval: int, max_budget: float, min_position_value: float = 0.03, blacklisted_token_ids: List[str] = None):
        """Updates strategy parameters dynamically."""
        self.stop_loss_pct = stop_loss
        self.take_profit_pct = take_profit
        self.min_share_price = min_price
        self.log_interval_minutes = log_interval
        self.max_budget = max_budget
        self.min_position_value = min_position_value
        if blacklisted_token_ids is not None:
            self.blacklisted_token_ids = set(blacklisted_token_ids)
        logger.info(f"🔄 PortfolioManager updated: SL={stop_loss*100:.1f}%, TP={take_profit*100:.1f}%, MinPrice={min_price}, MinPosVal=${min_position_value}, LogInt={log_interval}min, Budget=${max_budget}, BlacklistSize={len(self.blacklisted_token_ids)}")

    async def start(self):
        self._running = True
        logger.info("🧠 Portfolio Manager started.")
        # Start background risk monitor
        asyncio.create_task(self.monitor_risks())
        # Start portfolio logger
        asyncio.create_task(self.monitor_portfolio_logging())

    def stop(self):
        self._running = False

    async def on_trade_event(self, event: TradeEvent):
        """Callback for WhaleWatcher events"""
        try:
            # Fetch Metadata early for Logging
            metadata = await self.exchange.get_market_metadata(event.token_id)
            market_label = f"[{metadata.title} - {metadata.group_name or 'Outcome'}]"
            
            logger.info(f"🧠 Analyzing Event: {event.source_wallet_name} {event.side} {market_label}")
            
            # Check Blacklist
            if event.token_id in self.blacklisted_token_ids:
                logger.warning(f"  🛑 Token {event.token_id} ({market_label}) is blacklisted. Skipping trade.")
                return

            # --- FETCH ORDER BOOK ---
            depth = await self.exchange.get_order_book(event.token_id)
        
            if event.side == Side.BUY:
                await self._handle_buy_signal(event, market_label, depth)
            
            # We generally don't mirror sells blindly; we use our own exit logic.
            # But complex strategies might mirror sells too.
        except Exception as e:
            logger.error(f"Error processing trade event: {e}")


    async def _handle_buy_signal(self, event: TradeEvent, market_label: str, depth):
        # 0. Max Budget Check
        # We estimate cost. Real cost is size * price.
        # We need to calculate size first to know cost? Or check current_spend first?
        # Let's do a preliminary check or check right before sizing.
        # Actually checking raw balance is already done below.
        pass

        # 1. Budget Check (Wallet Balance)
        balance = await self.exchange.get_balance()
        if balance < 1.0:
            logger.warning("  Not enough funds to mirror.")
            return

        # 2. Position Check (REMOVED to allow repeated buys)
        # We now want to mirror repeatedly even if we hold the token.

        # 3. Size Logic (Minimum Amount Strategy)
        # We calculate the minimum viable size to avoid dust.

        
        # 4. Execute Buy (Market Buy via Limit)
        # Fetch current price to set limit
        try:
            # depth = await self.exchange.get_order_book(event.token_id) # Depth is now passed in
            if not depth.asks:
                logger.warning("  No sellers found.")
                return

            best_ask = min(a.price for a in depth.asks)
            
            # --- MIN PRICE FILTER ---
            if best_ask < self.min_share_price:
                logger.warning(f"  🛑 Price {best_ask:.2f} < Min {self.min_share_price:.2f}. Skipping mirror.")
                return
            # ------------------------

            from decimal import Decimal, ROUND_DOWN

            # Calculate limit price (slightly above best ask to ensure fill)
            limit_price_dec = Decimal(str(best_ask)).quantize(Decimal("0.01"), rounding=ROUND_DOWN)
            
            # Fixed minimum order amount in USD
            min_order_usd = Decimal("2.00")
            
            # Calculate size based on minimum order, rounded to 2 decimals
            size_dec = (min_order_usd / limit_price_dec).quantize(Decimal("0.01"), rounding=ROUND_DOWN)
            
            # Calculate cost estimate
            cost_rounded = (size_dec * limit_price_dec).quantize(Decimal("0.01"), rounding=ROUND_DOWN)
            
            # Convert to float for Order model
            limit_price = float(limit_price_dec)
            size = float(size_dec)
            cost_estimate = float(cost_rounded)
            
            # --- MAX BUDGET CHECK (Cumulative Spend) ---
            if self.cumulative_spend + cost_estimate > self.max_budget:
                logger.warning(f"  🛑 Max Budget Exceeded! Cumulative Spend (${self.cumulative_spend:.2f}) + Cost (${cost_estimate:.2f}) > Max: ${self.max_budget:.2f}")
                return
            # ------------------------
            
            logger.info(f"  ⚡ Mirroring Buy: {size:.2f} shares @ <{limit_price:.2f} {market_label}")
            
            # Simple buy execution (SmartExecutor usually optimized for exits, but simple buy here)
            # Create Order
            order = Order(
                token_id=event.token_id,
                side=Side.BUY,
                size=float(size_dec),               # keep Decimal result
                price_limit=float(limit_price_dec), # keep Decimal result
                market_name=market_label
            )

            await self.exchange.place_order(order)
            
            # Update Spend & Managed Tokens
            self.cumulative_spend += (size * limit_price)
            self.managed_tokens.add(event.token_id)
            self._save_state()
            logger.info(f"  💰 Spend Updated: Total ${self.cumulative_spend:.2f} / ${self.max_budget:.2f}")
            
        except Exception as e:
            logger.error(f"  Failed to mirror buy: {e}")

    async def monitor_risks(self):
        """Background loop to check Stop Loss / Take Profit"""
        while self._running:
            try:
                positions = await self.exchange.get_positions(min_value=self.min_position_value)
                if not positions:
                    await asyncio.sleep(60)
                    continue

                for pos in positions:
                    # Apply TP/SL to ALL positions (including pre-existing trades)
                    if pos.size <= 0:
                        continue
                    
                    try:
                        # Fetch rich metadata for human-readable logging
                        meta = await self.exchange.get_market_metadata(pos.token_id)
                        market_label = f"{meta.question}"
                        market_context = f"[{meta.category or 'Uncategorized'} | {meta.status or 'Unknown'}]"
                        
                        depth = await self.exchange.get_order_book(pos.token_id)
                        # Mark to market using best bid (exit price)
                        market_price = max(b.price for b in depth.bids) if depth.bids else 0.0
                        
                        if market_price == 0: continue # Illiquid

                        roi = (market_price - pos.average_entry_price) / pos.average_entry_price
                        
                        logger.debug(f"  Risk Check: {market_label} ROI: {roi*100:.1f}%")

                        # STOP LOSS
                        if roi < -self.stop_loss_pct:
                            pnl_emoji = "📉"
                            managed_tag = "🤖" if pos.token_id in self.managed_tokens else "📌"
                            logger.warning(f"{'='*60}")
                            logger.warning(f"🛑 STOP LOSS TRIGGERED")
                            logger.warning(f"   Q: {market_label}")
                            logger.warning(f"   {market_context}")
                            if meta.volume:
                                logger.warning(f"   Volume: ${meta.volume:,.2f} | Ends: {meta.end_date or 'N/A'}")
                            logger.warning(f"   {managed_tag} Position: {pos.size:.4f} shares @ Entry: {pos.average_entry_price:.3f} | Now: {market_price:.3f}")
                            logger.warning(f"   {pnl_emoji} ROI: {roi*100:.1f}% (Threshold: -{self.stop_loss_pct*100:.1f}%)")
                            logger.warning(f"{'='*60}")
                            
                            await self.executor.exit_position(
                                pos.token_id, 
                                pos.size, 
                                min_price=0.01,  # Dump it
                                market_name=market_label
                            )
                        
                        # TAKE PROFIT
                        elif roi > self.take_profit_pct:
                            pnl_emoji = "📈"
                            managed_tag = "🤖" if pos.token_id in self.managed_tokens else "📌"
                            logger.info(f"{'='*60}")
                            logger.info(f"💰 TAKE PROFIT TRIGGERED")
                            logger.info(f"   Q: {market_label}")
                            logger.info(f"   {market_context}")
                            if meta.volume:
                                logger.info(f"   Volume: ${meta.volume:,.2f} | Ends: {meta.end_date or 'N/A'}")
                            logger.info(f"   {managed_tag} Position: {pos.size:.4f} shares @ Entry: {pos.average_entry_price:.3f} | Now: {market_price:.3f}")
                            logger.info(f"   {pnl_emoji} ROI: {roi*100:.1f}% (Threshold: +{self.take_profit_pct*100:.1f}%)")
                            logger.info(f"{'='*60}")
                            
                            # Trailing stop logic could go here, but for now hard exit
                            await self.executor.exit_position(
                                pos.token_id, 
                                pos.size / 2,  # Sell half? Or all
                                min_price=market_price * 0.9,
                                market_name=market_label
                            )
                            
                    except Exception as e:
                        logger.error(f"Error monitoring {pos.token_id}: {e}")

            except Exception as e:
                logger.error(f"Error in risk monitor loop: {e}")
            
            await asyncio.sleep(60) # Run every minute

    async def monitor_portfolio_logging(self):
        """Periodically logs the portfolio summary."""
        while self._running:
            try:
                positions = await self.exchange.get_positions(min_value=self.min_position_value)
                if not positions:
                    logger.info("📊 Portfolio Report: No open positions.")
                else:
                    logger.info("=" * 60)
                    logger.info("📊 PORTFOLIO REPORT 📊")
                    logger.info("=" * 60)
                    total_value = 0.0
                    
                    for pos in positions:
                        # Log ALL positions (including pre-existing trades)
                        if pos.size <= 0:
                            continue
                        try:
                            # Fetch rich metadata for readability (like play.ipynb)
                            meta = await self.exchange.get_market_metadata(pos.token_id)
                            
                            # Fetch current market price
                            depth = await self.exchange.get_order_book(pos.token_id)
                            curr_price = max(b.price for b in depth.bids) if depth.bids else 0.0
                            
                            val = pos.size * curr_price
                            total_value += val
                            
                            pnl_pct = ((curr_price - pos.average_entry_price) / pos.average_entry_price) * 100 if pos.average_entry_price > 0 else 0
                            
                            # --- Rich Human-Readable Format (like play.ipynb) ---
                            logger.info(f"Q: {meta.question}")
                            logger.info(f"   [{meta.category or 'Uncategorized'} | {meta.status or 'Unknown'}]")
                            
                            # Show score for sports markets
                            if meta.score:
                                logger.info(f"   Score: {meta.score}")
                            
                            # Show volume and end date
                            vol_str = f"${meta.volume:,.2f}" if meta.volume else "N/A"
                            logger.info(f"   Volume: {vol_str} | Ends: {meta.end_date or 'N/A'}")
                            
                            # Show current outcome prices
                            if meta.outcomes:
                                logger.info("   Market Prices:")
                                for outcome, price in meta.outcomes.items():
                                    logger.info(f"     - {outcome}: {price:.3f}")
                            
                            # Show position details with PnL
                            pnl_emoji = "📈" if pnl_pct >= 0 else "📉"
                            managed_tag = "🤖" if pos.token_id in self.managed_tokens else "📌"
                            logger.info(f"   {managed_tag} YOUR POSITION: {pos.size:.2f} shares @ Entry: {pos.average_entry_price:.3f} | Now: {curr_price:.3f} | {pnl_emoji} PnL: {pnl_pct:+.1f}% | Value: ${val:.2f}")
                            logger.info("-" * 60)
                            
                        except Exception as e:
                            logger.error(f"Error reporting on pos {pos.token_id}: {e}")
                    
                    logger.info(f"💰 TOTAL PORTFOLIO VALUE: ${total_value:.2f}")
                    logger.info("=" * 60)
                
                # Sleep interval
                await asyncio.sleep(self.log_interval_minutes * 60)

            except Exception as e:
                logger.error(f"Error in portfolio logger: {e}")
                await asyncio.sleep(60) # Retry sooner on error
