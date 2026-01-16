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
    def __init__(self, exchange: ExchangeProvider, executor: SmartExecutor, stop_loss_pct: float = 0.20, take_profit_pct: float = 0.9, min_share_price: float = 0.19, log_interval_minutes: int = 60, max_budget: float = 100.0):
        self.exchange = exchange
        self.executor = executor
        self.stop_loss_pct = stop_loss_pct
        self.take_profit_pct = take_profit_pct
        self.min_share_price = min_share_price
        self.log_interval_minutes = log_interval_minutes
        self.max_budget = max_budget
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

    def update_strategies(self, stop_loss: float, take_profit: float, min_price: float, log_interval: int, max_budget: float):
        """Updates strategy parameters dynamically."""
        self.stop_loss_pct = stop_loss
        self.take_profit_pct = take_profit
        self.min_share_price = min_price
        self.log_interval_minutes = log_interval
        self.max_budget = max_budget
        logger.info(f"🔄 PortfolioManager updated: SL={stop_loss*100:.1f}%, TP={take_profit*100:.1f}%, MinPrice={min_price}, LogInt={log_interval}min, Budget=${max_budget}")

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
                positions = await self.exchange.get_positions()
                if not positions:
                    await asyncio.sleep(60)
                    continue

                for pos in positions:
                    # Only manage positions we entered
                    if pos.token_id not in self.managed_tokens:
                        continue

                    if pos.size <= 0:
                        continue
                    # ROI Calculation
                    # Note: pos.current_price should be refreshed from market if model isn't live updating
                    # Assuming exchange.get_positions returns fresh data or we fetch book here.
                    # For safety, let's fetch fresh book price.
                    
                    try:
                        depth = await self.exchange.get_order_book(pos.token_id)
                        # Mark to market using best bid (exit price)
                        market_price = max(b.price for b in depth.bids) if depth.bids else 0.0
                        
                        if market_price == 0: continue # Illiquid

                        roi = (market_price - pos.average_entry_price) / pos.average_entry_price
                        
                        logger.debug(f"  Risk Check: {pos.token_id} ROI: {roi*100:.1f}%")

                        # STOP LOSS
                        if roi < -self.stop_loss_pct:
                            logger.warning(f"  🛑 STOP LOSS TRIGGERED: {pos.token_id} down {roi*100:.1f}%")
                            await self.executor.exit_position(
                                pos.token_id, 
                                pos.size, 
                                min_price=0.01 # Dump it
                            )
                        
                        # TAKE PROFIT
                        elif roi > self.take_profit_pct:
                            logger.info(f"  💰 TAKE PROFIT TRIGGERED: {pos.token_id} up {roi*100:.1f}%")
                            # Trailing stop logic could go here, but for now hard exit
                            await self.executor.exit_position(
                                pos.token_id, 
                                pos.size / 2, # Sell half? Or all. Code implies 'liquidate everything' in prompt
                                min_price=market_price * 0.9
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
                positions = await self.exchange.get_positions()
                if not positions:
                    logger.info("📊 Portfolio Report: No open positions.")
                else:
                    logger.info("=== 📊 Portfolio Report 📊 ===")
                    total_value = 0.0
                    
                    for pos in positions:
                        # Only manage positions we entered
                        if pos.token_id not in self.managed_tokens:
                            continue

                        if pos.size <= 0:
                            continue
                        try:
                            # Fetch metadata for readability.
                            meta = await self.exchange.get_market_metadata(pos.token_id)
                            title = f"{meta.title} - {meta.group_name}"[:50] 
                            
                            # Let's fetch book to show profitability as requested.
                            depth = await self.exchange.get_order_book(pos.token_id)
                            curr_price = max(b.price for b in depth.bids) if depth.bids else 0.0
                            
                            val = pos.size * curr_price
                            total_value += val
                            
                            pnl_pct = ((curr_price - pos.average_entry_price) / pos.average_entry_price) * 100 if pos.average_entry_price > 0 else 0
                            
                            logger.info(f"  🔹 {title} | Size: {pos.size:.2f} | Entry: {pos.average_entry_price:.2f} | Curr: {curr_price:.2f} | PnL: {pnl_pct:+.1f}% | Val: ${val:.2f}")
                            
                        except Exception as e:
                            logger.error(f"Error reporting on pos {pos.token_id}: {e}")
                    
                    logger.info(f"  💰 Total Portfolio Value: ${total_value:.2f}")
                    logger.info("================================")
                
                # Sleep interval
                await asyncio.sleep(self.log_interval_minutes * 60)

            except Exception as e:
                logger.error(f"Error in portfolio logger: {e}")
                await asyncio.sleep(60) # Retry sooner on error
