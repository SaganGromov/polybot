import asyncio
import logging
import math
import json
import os
from typing import List, Set, Optional, TYPE_CHECKING
from polybot.core.interfaces import ExchangeProvider, MarketMetadata
from polybot.core.models import Position, Side, Order, OrderStatus
from polybot.core.events import TradeEvent
from polybot.services.execution import SmartExecutor
from polybot.config.settings import settings

if TYPE_CHECKING:
    from polybot.services.ai_analysis_service import AIAnalysisService

logger = logging.getLogger(__name__)

class PortfolioManager:
    def __init__(self, exchange: ExchangeProvider, executor: SmartExecutor, stop_loss_pct: float = 0.20, take_profit_pct: float = 0.9, min_share_price: float = 0.19, log_interval_minutes: int = 60, max_budget: float = 100.0, min_position_value: float = 0.03, blacklisted_token_ids: List[str] = None, ai_service: Optional['AIAnalysisService'] = None, risk_check_interval_seconds: int = 10):
        self.exchange = exchange
        self.executor = executor
        self.stop_loss_pct = stop_loss_pct
        self.take_profit_pct = take_profit_pct
        self.min_share_price = min_share_price
        self.log_interval_minutes = log_interval_minutes
        self.max_budget = max_budget
        self.min_position_value = min_position_value
        self.blacklisted_token_ids = set(blacklisted_token_ids or [])
        self.risk_check_interval_seconds = risk_check_interval_seconds
        self._running = False
        
        # AI Analysis Integration
        self.ai_service = ai_service
        self.ai_enabled = False
        self.ai_block_on_negative = True
        self.ai_min_confidence = 0.6
        
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

    def update_strategies(self, stop_loss: float, take_profit: float, min_price: float, log_interval: int, max_budget: float, min_position_value: float = 0.03, blacklisted_token_ids: List[str] = None, risk_check_interval_seconds: int = None):
        """Updates strategy parameters dynamically."""
        self.stop_loss_pct = stop_loss
        self.take_profit_pct = take_profit
        self.min_share_price = min_price
        self.log_interval_minutes = log_interval
        self.max_budget = max_budget
        self.min_position_value = min_position_value
        if blacklisted_token_ids is not None:
            self.blacklisted_token_ids = set(blacklisted_token_ids)
        if risk_check_interval_seconds is not None:
            self.risk_check_interval_seconds = risk_check_interval_seconds
        logger.info(f"🔄 PortfolioManager updated: SL={stop_loss*100:.1f}%, TP={take_profit*100:.1f}%, MinPrice={min_price}, MinPosVal=${min_position_value}, LogInt={log_interval}min, Budget=${max_budget}, BlacklistSize={len(self.blacklisted_token_ids)}, RiskCheck={self.risk_check_interval_seconds}s")

    def update_ai_config(self, enabled: bool, block_on_negative: bool, min_confidence: float):
        """Updates AI analysis configuration."""
        self.ai_enabled = enabled
        self.ai_block_on_negative = block_on_negative
        self.ai_min_confidence = min_confidence
        status = "ENABLED" if enabled else "DISABLED"
        logger.info(f"🤖 AI Analysis: {status} (block_on_negative={block_on_negative}, min_confidence={min_confidence:.0%})")

    async def start(self):
        self._running = True
        logger.info("🧠 Portfolio Manager started.")
        # Start background risk monitor
        asyncio.create_task(self.monitor_risks())
        # Start portfolio logger
        asyncio.create_task(self.monitor_portfolio_logging())

    def stop(self):
        self._running = False

    async def _prompt_manual_override(self, market_label: str, analysis, token_id: str = "") -> bool:
        """
        Prompt for manual override when AI rejects a trade.
        Returns True if user approves, False if timeout or rejection.
        
        Uses a file-based approach for Docker compatibility:
        - Creates an override request file
        - Waits 10 seconds for user to create approval file
        - User runs: docker exec polybot-bot-1 touch /tmp/approve
        """
        import os
        import sys
        
        # File paths for override mechanism
        override_dir = "/tmp/polybot_override"
        approve_file = os.path.join(override_dir, "approve")
        
        # Clean up any stale approval file
        os.makedirs(override_dir, exist_ok=True)
        if os.path.exists(approve_file):
            os.remove(approve_file)
        
        # Log the override prompt
        print("\n" + "=" * 60)
        print("🚨 MANUAL OVERRIDE REQUIRED 🚨")
        print(f"   Market: {market_label}")
        print(f"   AI Confidence: {analysis.confidence:.0%}")
        print(f"   Risks: {', '.join(analysis.risk_factors[:3]) if analysis.risk_factors else 'None identified'}")
        print("=" * 60)
        print("⏰ To APPROVE this trade within 10 seconds, run:")
        print(f"   docker exec polybot-bot-1 touch {approve_file}")
        print("   (Otherwise trade will be skipped automatically)")
        print("=" * 60)
        sys.stdout.flush()
        
        try:
            # Poll for approval file for 10 seconds
            for i in range(20):  # Check every 0.5 seconds for 10 seconds
                await asyncio.sleep(0.5)
                
                if os.path.exists(approve_file):
                    # Clean up and approve
                    os.remove(approve_file)
                    print("✅ Override approved! (approval file detected)")
                    return True
            
            print("⏰ Timeout - no approval received, skipping trade")
            return False
                
        except Exception as e:
            logger.error(f"Error in manual override prompt: {e}")
            return False

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
            
            # Check Sports Filter
            if self.ai_service:
                logger.info(f"  🏈 Sports filter check: enabled={self.ai_service.sports_filter_enabled}, category='{metadata.category}'")
                if self.ai_service.sports_filter_enabled:
                    try:
                        is_sports, reason = await self.ai_service.check_sports_filter(
                            event.token_id, metadata
                        )
                        if is_sports:
                            logger.warning(f"  🏈 BLOCKED - Sports market: {reason}")
                            return
                        else:
                            logger.info(f"  ✅ Not sports: {reason}")
                    except Exception as e:
                        logger.error(f"  Sports filter check failed: {e} - proceeding with trade")

            # --- FETCH ORDER BOOK ---
            depth = await self.exchange.get_order_book(event.token_id)
        
            if event.side == Side.BUY:
                await self._handle_buy_signal(event, market_label, depth, metadata)
            
            # We generally don't mirror sells blindly; we use our own exit logic.
            # But complex strategies might mirror sells too.
        except Exception as e:
            logger.error(f"Error processing trade event: {e}")


    async def _handle_buy_signal(self, event: TradeEvent, market_label: str, depth, metadata: MarketMetadata = None):
        # 0. AI Analysis Gate
        if self.ai_service and self.ai_enabled:
            try:
                should_trade, analysis = await self.ai_service.should_execute_trade(
                    token_id=event.token_id,
                    trade_event=event,
                    market_metadata=metadata,
                    market_depth=depth
                )
                
                if should_trade:
                    # AI approves - auto-proceed
                    logger.info(f"  🤖 AI Analysis: ✅ PROCEED (confidence: {analysis.confidence:.0%})")
                else:
                    # AI rejects - ask for manual override with timeout
                    if analysis.confidence >= self.ai_min_confidence:
                        logger.warning(f"  🤖 AI recommends SKIP (confidence: {analysis.confidence:.0%})")
                        logger.warning(f"     Reason: {analysis.justification}")
                        
                        # Prompt for manual override with 10 second timeout
                        override = await self._prompt_manual_override(market_label, analysis)
                        
                        if not override:
                            logger.info(f"  ⏭️ Trade skipped (no manual override)")
                            return
                        else:
                            logger.info(f"  👤 Manual override accepted - proceeding with trade")
                    else:
                        logger.info(f"  🤖 AI recommends skip but low confidence ({analysis.confidence:.0%}), auto-proceeding")
            except Exception as e:
                logger.error(f"  AI analysis failed: {e} - proceeding with trade")

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
            
            # Polymarket minimum order size is 5 shares
            min_size = Decimal("5.00")
            
            # Calculate size based on minimum order, rounded to 2 decimals
            size_dec = (min_order_usd / limit_price_dec).quantize(Decimal("0.01"), rounding=ROUND_DOWN)
            
            # Ensure we never buy less than the minimum size required by Polymarket
            if size_dec < min_size:
                size_dec = min_size
            
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
        """Background loop to check Stop Loss / Take Profit with parallel position checks."""
        while self._running:
            try:
                positions = await self.exchange.get_positions(min_value=self.min_position_value)
                if not positions:
                    await asyncio.sleep(self.risk_check_interval_seconds)
                    continue

                # Filter valid positions first
                valid_positions = [pos for pos in positions if pos.size > 0]
                
                if valid_positions:
                    # Check all positions in parallel for faster TP/SL detection
                    await asyncio.gather(
                        *[self._check_position_risk(pos) for pos in valid_positions],
                        return_exceptions=True  # Don't fail all checks if one fails
                    )

            except Exception as e:
                logger.error(f"Error in risk monitor loop: {e}")
            
            await asyncio.sleep(self.risk_check_interval_seconds)
    
    async def _check_position_risk(self, pos: Position):
        """Check a single position for stop loss or take profit triggers."""
        try:
            # Fetch rich metadata for human-readable logging
            meta = await self.exchange.get_market_metadata(pos.token_id)
            market_label = f"{meta.question}"
            market_context = f"[{meta.category or 'Uncategorized'} | {meta.status or 'Unknown'}]"
            
            # Get current price from Gamma API (more accurate than order book)
            # Falls back to order book if metadata is unavailable
            market_price = 0.0
            if meta.outcomes and meta.queried_outcome and meta.queried_outcome in meta.outcomes:
                market_price = meta.outcomes[meta.queried_outcome]
            else:
                # Fallback to order book
                depth = await self.exchange.get_order_book(pos.token_id)
                market_price = max(b.price for b in depth.bids) if depth.bids else 0.0
            
            if market_price == 0:
                return  # Illiquid, skip

            roi = (market_price - pos.average_entry_price) / pos.average_entry_price
            
            logger.debug(f"  Risk Check: {market_label} ROI: {roi*100:.1f}%")

            # STOP LOSS
            if roi < -self.stop_loss_pct:
                pnl_emoji = "📉"
                managed_tag = "🤖" if pos.token_id in self.managed_tokens else "📌"
                outcome_label = f" ({meta.queried_outcome})" if meta.queried_outcome else ""
                logger.warning(f"{'='*60}")
                logger.warning(f"🛑 STOP LOSS TRIGGERED")
                logger.warning(f"   Q: {market_label}")
                logger.warning(f"   {market_context}")
                if meta.volume:
                    logger.warning(f"   Volume: ${meta.volume:,.2f} | Ends: {meta.end_date or 'N/A'}")
                logger.warning(f"   {managed_tag} Position{outcome_label}: {pos.size:.4f} shares @ Entry: {pos.average_entry_price:.3f} | Now: {market_price:.3f}")
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
                outcome_label = f" ({meta.queried_outcome})" if meta.queried_outcome else ""
                logger.info(f"{'='*60}")
                logger.info(f"💰 TAKE PROFIT TRIGGERED")
                logger.info(f"   Q: {market_label}")
                logger.info(f"   {market_context}")
                if meta.volume:
                    logger.info(f"   Volume: ${meta.volume:,.2f} | Ends: {meta.end_date or 'N/A'}")
                logger.info(f"   {managed_tag} Position{outcome_label}: {pos.size:.4f} shares @ Entry: {pos.average_entry_price:.3f} | Now: {market_price:.3f}")
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
                            
                            # Get current price from Gamma API (more accurate than order book)
                            # Falls back to order book if metadata is unavailable
                            curr_price = 0.0
                            if meta.outcomes and meta.queried_outcome and meta.queried_outcome in meta.outcomes:
                                curr_price = meta.outcomes[meta.queried_outcome]
                            else:
                                # Fallback to order book
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
                            
                            # Show current outcome prices (highlight user's position)
                            if meta.outcomes:
                                logger.info("   Market Prices:")
                                for outcome, price in meta.outcomes.items():
                                    marker = " ← YOU" if outcome == meta.queried_outcome else ""
                                    logger.info(f"     - {outcome}: {price:.3f}{marker}")
                            
                            # Show position details with PnL (include outcome name)
                            pnl_emoji = "📈" if pnl_pct >= 0 else "📉"
                            managed_tag = "🤖" if pos.token_id in self.managed_tokens else "📌"
                            outcome_label = f" ({meta.queried_outcome})" if meta.queried_outcome else ""
                            logger.info(f"   {managed_tag} YOUR POSITION{outcome_label}: {pos.size:.2f} shares @ Entry: {pos.average_entry_price:.3f} | Now: {curr_price:.3f} | {pnl_emoji} PnL: {pnl_pct:+.1f}% | Value: ${val:.2f}")
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
