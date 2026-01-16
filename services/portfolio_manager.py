import asyncio
import logging
from typing import List
from polybot.core.interfaces import ExchangeProvider
from polybot.core.models import Position, Side, Order, OrderStatus
from polybot.core.events import TradeEvent
from polybot.services.execution import SmartExecutor
from polybot.config.settings import settings

logger = logging.getLogger(__name__)

class PortfolioManager:
    def __init__(self, exchange: ExchangeProvider, executor: SmartExecutor, stop_loss_pct: float = 0.20, take_profit_pct: float = 0.9):
        self.exchange = exchange
        self.executor = executor
        self.stop_loss_pct = stop_loss_pct
        self.take_profit_pct = take_profit_pct
        self._running = False
        self.global_max_spend = 100.0 # From config/env ideally
        self.current_spend = 0.0

    async def start(self):
        self._running = True
        logger.info("🧠 Portfolio Manager started.")
        # Start background risk monitor
        asyncio.create_task(self.monitor_risks())

    def stop(self):
        self._running = False

    async def on_trade_event(self, event: TradeEvent):
        """Callback for WhaleWatcher events"""
        logger.info(f"🧠 Analyzing Event: {event.source_wallet_name} {event.side} {event.token_id}")

        if event.side == Side.BUY:
            await self._handle_buy_signal(event)
        
        # We generally don't mirror sells blindly; we use our own exit logic.
        # But complex strategies might mirror sells too.

    async def _handle_buy_signal(self, event: TradeEvent):
        # 1. Budget Check
        balance = await self.exchange.get_balance()
        if balance < 1.0:
            logger.warning("  Not enough funds to mirror.")
            return

        # 2. Position Check (Don't double buy for now)
        # positions = await self.exchange.get_positions()
        # if any(p.token_id == event.token_id for p in positions):
        #     logger.info("  Already holding this token. Skipping.")
        #     return

        # 3. Size Logic (Fixed or Proportional)
        # For prototype: Invest fixed $10 or max available
        wager = 2
        
        # 4. Execute Buy (Market Buy via Limit)
        # Fetch current price to set limit
        try:
            depth = await self.exchange.get_order_book(event.token_id)
            if not depth.asks:
                logger.warning("  No sellers found.")
                return
            
            best_ask = min(a.price for a in depth.asks)
            limit_price = min(best_ask + 0.05, 0.99) # 5 cent slippage tolerance
            # Round down to 2 decimals to satisfy API precision requirements (maker amount max 2 decimals)
            import math
            size = math.floor((wager / limit_price) * 100) / 100.0
            
            logger.info(f"  ⚡ Mirroring Buy: {size:.2f} shares @ <{limit_price:.2f}")
            
            # Simple buy execution (SmartExecutor usually optimized for exits, but simple buy here)
            # We could add a smart_entry to executor, but for now direct order:
            order = Order(
                token_id=event.token_id,
                side=Side.BUY,
                size=size,
                price_limit=limit_price
            )
            await self.exchange.place_order(order)
            self.current_spend += wager
            
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
