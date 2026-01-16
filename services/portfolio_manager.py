import asyncio
import logging
import math
import json
import os
from typing import List
from polybot.core.interfaces import ExchangeProvider
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
        self._load_state()

    def _load_state(self):
        if os.path.exists(self.state_file):
            try:
                with open(self.state_file, 'r') as f:
                    data = json.load(f)
                    self.cumulative_spend = data.get("cumulative_spend", 0.0)
                logger.info(f"💾 Loaded Bot State: Cumulative Spend=${self.cumulative_spend:.2f}")
            except Exception as e:
                logger.error(f"Failed to load bot state: {e}")

    def _save_state(self):
        try:
            data = {"cumulative_spend": self.cumulative_spend}
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
                logger.warning(f"  🛑 Price {best_ask:.2f} < Min {self.min_share_price}. Skipping mirror.")
                return
            # ------------------------

            limit_price = min(best_ask + 0.05, 0.99) # 5 cent slippage tolerance

            # Calculate Minimum Size
            # Strategy: Use the greater of (Orderbook Min Size) or ($1.00 USD worth of shares)
            # This ensures we don't buy dust (below min size) and satisfied API min cost requirements ($1 typically)
            
            min_size_shares = depth.min_order_size
            shares_for_one_dollar = 1.0 / limit_price if limit_price > 0 else 0
            
            # Target size is the larger of the two constraints
            target_size = max(min_size_shares, shares_for_one_dollar)
            
            # Round UP slightly to be safe? No, floor to 2 decimals is safe for API, 
            # but we must ensure we aren't flooring BELOW the min_size.
            # So we add a tiny buffer (0.01) then floor, or just use 2 decimal rounding carefully.
            
            import math
            # Example: target is 2.123 -> 2.12. If min is 2.123, 2.12 is invalid. 
            # So actually we should ceil to 2 decimals if it's strictly a minimum?
            # Polymarket API usually truncates. Let's try 1.01 * min to be safe.
            
            raw_target = target_size * 1.01
            size = math.floor(raw_target * 100) / 100.0
            
            # Limit logic
            cost_estimate = size * limit_price
            
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
                size=size,
                limit_price=limit_price,
                market_name=market_label
            )
            await self.exchange.place_order(order)
            
            # Update Spend
            self.cumulative_spend += (size * limit_price)
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
