import asyncio
import logging
import math
from polybot.core.interfaces import ExchangeProvider
from polybot.core.models import Order, Side, OrderStatus
from polybot.core.errors import ExchangeError

logger = logging.getLogger(__name__)

class SmartExecutor:
    def __init__(self, exchange: ExchangeProvider, slippage_tolerance_bps: int = 75):
        self.exchange = exchange
        self.slippage_tolerance_bps = slippage_tolerance_bps

    # TODO: Add logic to handle cases where token_id is invalid or network issues occur gracefully
    async def exit_position(self, token_id: str, total_size: float, min_price: float, max_sweeps: int = 6, delay_seconds: float = 1.0) -> float:
        """
        Smartly exits a position by dripping liquidity to avoid crashing the price.
        
        Args:
            token_id: The asset to sell.
            total_size: Total shares to sell.
            min_price: Floor price; won't sell below this.
            max_sweeps: Maximum number of partial fill attempts.
            delay_seconds: Time to wait between sweeps.

        Returns:
            float: Total shares sold.
        """
        remaining = total_size
        sold_total = 0.0

        logger.info(f"📉 Starting Smart Exit for {token_id}. Size: {total_size}, Floor: {min_price}")

        for sweep in range(1, max_sweeps + 1):
            if remaining <= 0:
                break
                
            try:
                # 1. Fetch Orderbook
                depth = await self.exchange.get_order_book(token_id)
                bids = depth.bids
                
                if not bids:
                    logger.warning(f"  [Sweep {sweep}] No bids available.")
                    break

                # 2. Calculate Fillable Liquidity above min_price
                fillable_qty = 0.0
                
                # Sort bids high to low (best price first)
                sorted_bids = sorted(bids, key=lambda x: x.price, reverse=True)
                
                for bid in sorted_bids:
                    if bid.price < min_price:
                        break # Stop if price is too low
                    fillable_qty += bid.size

                # 3. Determine Chunk Size
                # We sell min(remaining, what the market can take)
                chunk_size = min(remaining, fillable_qty)
                
                # Rounding logic (simple floor to 2 decimals)
                chunk_size = math.floor(chunk_size * 100) / 100.0
                
                if chunk_size <= 0:
                    logger.info(f"  [Sweep {sweep}] No liquidity above {min_price}. Waiting...")
                else:
                    # 4. Execute Sell
                    # We accept the slippage implied by min_price
                    # Using min_price as the limit ensures we don't cross our floor
                    logger.info(f"  [Sweep {sweep}] Selling {chunk_size} shares...")
                    
                    order = Order(
                        token_id=token_id,
                        side=Side.SELL,
                        size=chunk_size,
                        price_limit=min_price, # FOK Limit
                        status=OrderStatus.PENDING
                    )
                    
                    order_id = await self.exchange.place_order(order)
                    logger.info(f"    -> Filled (ID: {order_id})")
                    
                    sold_total += chunk_size
                    remaining -= chunk_size
                    
            except ExchangeError as e:
                logger.error(f"  [Sweep {sweep}] Exchange Error: {e}")
                
            except Exception as e:
                logger.error(f"  [Sweep {sweep}] Unexpected Error: {e}")

            # 5. Drip Delay
            if remaining > 0 and sweep < max_sweeps:
                await asyncio.sleep(delay_seconds)

        leftover = total_size - sold_total
        if leftover > 0:
            logger.warning(f"⚠️ Exit incomplete. Sold {sold_total}/{total_size}. Remaining: {leftover}")
        else:
            logger.info(f"✅ Position closed successfully. Sold {sold_total}.")

        return sold_total
