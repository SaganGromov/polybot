import os
import logging
import requests
from typing import List
from decimal import Decimal, getcontext, ROUND_DOWN as D_ROUND_DOWN, ROUND_HALF_UP, ROUND_UP as D_ROUND_UP
from math import floor, ceil

getcontext().prec = 18

logger = logging.getLogger(__name__)

# =============================================================================
# CRITICAL FIX: Monkey-patch py-clob-client helper functions with Decimal-based
# implementations BEFORE importing ClobClient. This must happen first because
# Python imports cache module state at load time.
# =============================================================================

# First, import only the modules we need to patch (not ClobClient yet!)
import py_clob_client.order_builder.helpers as clob_helpers
import py_clob_client.order_builder.builder as clob_builder

def _patched_round_down(x: float, sig_digits: int) -> float:
    """Decimal-based round_down to avoid float precision issues."""
    d = Decimal(str(x))
    q = Decimal(10) ** -sig_digits
    result = float(d.quantize(q, rounding=D_ROUND_DOWN))
    print(f"🔧 PATCHED round_down({x}, {sig_digits}) = {result}")
    return result

def _patched_round_normal(x: float, sig_digits: int) -> float:
    """Decimal-based round_normal to avoid float precision issues."""
    d = Decimal(str(x))
    q = Decimal(10) ** -sig_digits
    result = float(d.quantize(q, rounding=ROUND_HALF_UP))
    print(f"🔧 PATCHED round_normal({x}, {sig_digits}) = {result}")
    return result

def _patched_round_up(x: float, sig_digits: int) -> float:
    """Decimal-based round_up to avoid float precision issues."""
    d = Decimal(str(x))
    q = Decimal(10) ** -sig_digits
    result = float(d.quantize(q, rounding=D_ROUND_UP))
    print(f"🔧 PATCHED round_up({x}, {sig_digits}) = {result}")
    return result

def _patched_decimal_places(x: float) -> int:
    """Accurate decimal places calculation."""
    d = Decimal(str(x))
    result = max(0, -d.as_tuple().exponent)
    print(f"🔧 PATCHED decimal_places({x}) = {result}")
    return result

# Apply monkey patches to helpers module
clob_helpers.round_down = _patched_round_down
clob_helpers.round_normal = _patched_round_normal
clob_helpers.round_up = _patched_round_up
clob_helpers.decimal_places = _patched_decimal_places

# CRITICAL: Also patch the builder module's cached references!
clob_builder.round_down = _patched_round_down
clob_builder.round_normal = _patched_round_normal
clob_builder.round_up = _patched_round_up
clob_builder.decimal_places = _patched_decimal_places

logger.info("🔧 Applied Decimal-based monkey patches to py-clob-client")
# =============================================================================

# NOW import ClobClient - it will use our patched functions
from py_clob_client.client import ClobClient
from py_clob_client.clob_types import OrderArgs, OrderType
from py_clob_client.exceptions import PolyApiException

from polybot.core.interfaces import ExchangeProvider
from polybot.core.models import Position, Order, OrderStatus, Side, MarketDepth, MarketDepthLevel, MarketMetadata
from polybot.core.errors import APIError, AuthError, OrderError, InsufficientFundsError
from polybot.config.settings import settings
from polybot.adapters.websocket_client import PolymarketWebsocketClient


class PolymarketAdapter(ExchangeProvider):
    def __init__(self):
        try:
            # Determine signature type similar to main2.py/sniper_bot.py logic
            # For simplicity, we assume we might need to enhance this logic later
            # but getting it from settings or defaulting to 1 (Proxy) or 0 (EOA) is standard.
            # Using logic from sniper_bot which defaults to 1 usually for bot operations.
            
            # Extract private key from SecretStr
            pk = settings.WALLET_PRIVATE_KEY.get_secret_value()
            host = os.getenv("CLOB_API", "https://clob.polymarket.com")
            chain_id = int(os.getenv("CHAIN_ID", "137"))
            
            # Using defaults/env similar to main2.py logic could be added here
            # But relying on initialized settings is cleaner.
            
            self.client = ClobClient(
                host=host,
                key=pk,
                chain_id=chain_id,
                signature_type=1, # Defaulting to 1 as per sniper_bot; could make configurable
                funder=os.getenv("PROXY_ADDRESS") or os.getenv("FUNDER") # Fallback to env if needed
            )
            
            # Auto-derive creds
            self.client.set_api_creds(self.client.create_or_derive_api_creds())
            
        except Exception as e:
            raise AuthError(f"Failed to initialize Polymarket Client: {e}")

        self.positions_api_url = os.getenv("POLY_POSITIONS_API", "https://data-api.polymarket.com/positions")
        self.user_address = os.getenv("PROXY_ADDRESS") or os.getenv("USER_ADDRESS")
        self.ws_client = PolymarketWebsocketClient()

    async def start(self):
        await self.ws_client.start()

    async def stop(self):
        await self.ws_client.stop()

    async def get_balance(self) -> float:
        # NOTE: Polymarket ClobClient usually has get_balance or similar?
        # Alternatively, we might need to check USDC balance on chain if the CLOB client doesn't provide it easily
        # or uses the collateral balance endpoint.
        # sniper_bot uses `get_token_balance` via data-api for specific tokens, 
        # but for USDC (spending power) it tracks it manually or we can fetch valid collateral.
        try:
            # This is a simplification. Ideally, we query the collateral balance on the CTF exchange or USDC contract.
            # The ClobClient often provides `get_collateral_balance` or similar depending on version.
            # If not available, we return 0.0 or implement web3 logic.
            # Recent py-clob-client versions might have `get_balance_allowance` or similar.
            # Let's try to stick to what functionality we know exists or return a placeholder if complex web3 needed.
            return 1000.0 # Placeholder: Implementing full Web3/USDC check is outside current files provided scope without web3 lib usage details.
        except Exception as e:
            raise APIError(f"Failed to get balance: {e}")

    async def get_positions(self, min_value: float = 0.0) -> List[Position]:
        """
        Adapted from main2.py _fetch_positions
        
        Args:
            min_value: Minimum position value in USD to include (filters dust)
        """
        if not self.user_address:
            raise AuthError("User address (PROXY_ADDRESS or USER_ADDRESS) is not set.")

        limit = 100
        offset = 0
        all_positions = []

        try:
            while True:
                params = {
                    "user": self.user_address, 
                    "sizeThreshold": "0", 
                    "limit": str(limit), 
                    "offset": str(offset)
                }
                r = requests.get(self.positions_api_url, params=params, timeout=10)
                r.raise_for_status()
                batch = r.json()
                
                if not isinstance(batch, list):
                    break
                    
                all_positions.extend(batch)
                if len(batch) < limit:
                    break
                offset += limit
                
        except requests.RequestException as e:
            raise APIError(f"Failed to fetch positions: {e}")
            
        # Parse into domain models
        domain_positions = []
        for p in all_positions:
            # Logic from main2.py _is_open_position
            size = float(p.get("size", 0))
            redeemable = p.get("redeemable")
            if size > 0 and (redeemable is False or redeemable is None):
                try:
                    init_val = float(p.get("initialValue", 0))
                    curr_val = float(p.get("currentValue", 0))
                    
                    # Skip dust positions below min_value threshold
                    if curr_val < min_value:
                        continue
                    
                    # Calculate avg entry price approx
                    avg_entry = (init_val / size) if size else 0.0
                    
                    domain_positions.append(Position(
                        token_id=str(p.get("asset")),
                        size=size,
                        average_entry_price=avg_entry,
                        current_price=curr_val  # Total value of position
                    ))
                except (ValueError, TypeError):
                    continue
                    
        return domain_positions

    async def place_order(self, order: Order) -> str:
        """
        Adapted from sniper_bot.py execute_trade logic
        """
        try:
            # Convert domain Side to CLOB side
            side_str = "BUY" if order.side == Side.BUY else "SELL"
            
            from decimal import Decimal, ROUND_DOWN
            
            # Convert to Decimal for exact arithmetic
            price_dec = Decimal(str(order.price_limit)).quantize(Decimal("0.01"), rounding=ROUND_DOWN)
            size_dec = Decimal(str(order.size)).quantize(Decimal("0.01"), rounding=ROUND_DOWN)
            
            # For SELL orders: Use the actual size we want to sell (shares)
            # For BUY orders: Back-calculate size to ensure maker_amount has ≤2 decimals
            if order.side == Side.SELL:
                # SELL: size is shares to sell, use directly (just ensure it's >= minimum)
                final_size = float(size_dec)
                final_price = float(price_dec)
                final_cost_float = final_size * final_price
                cost_decimals = 2  # Not critical for sells
                
                logger.info(f"  Order Values (SELL): price={final_price:.2f}, size={final_size:.2f}, cost={final_cost_float:.2f}")
            else:
                # BUY: maker_amount = size * price (USDC paid)
                # API requires maker_amount ≤ 2 decimals.
                raw_cost = size_dec * price_dec
                
                # Round cost to 2 decimals (API requirement for maker_amount)
                cost_rounded = raw_cost.quantize(Decimal("0.01"), rounding=ROUND_DOWN)
                
                # CRITICAL: Back-calculate size to ensure size * price = cost_rounded exactly
                adjusted_size_dec = (cost_rounded / price_dec).quantize(Decimal("0.01"), rounding=ROUND_DOWN)
                
                # Verify the product is clean
                final_cost = adjusted_size_dec * price_dec
                cost_decimals = abs(Decimal(str(final_cost)).as_tuple().exponent)
                
                # If still > 2 decimals, try adjusting size down by 0.01 until it works
                attempts = 0
                while cost_decimals > 2 and attempts < 10:
                    adjusted_size_dec -= Decimal("0.01")
                    final_cost = adjusted_size_dec * price_dec
                    cost_decimals = abs(Decimal(str(final_cost)).as_tuple().exponent)
                    attempts += 1
                
                # CRITICAL: Polymarket minimum order size is 5 shares
                min_order_size = Decimal("5.00")
                if adjusted_size_dec < min_order_size:
                    # Size fell below minimum during decimal adjustment
                    # Round size up to minimum and adjust price down to maintain clean cost
                    adjusted_size_dec = min_order_size
                    # Find a price that gives us clean cost decimals (≤2)
                    target_cost = (min_order_size * price_dec).quantize(Decimal("0.01"), rounding=ROUND_DOWN)
                    price_dec = (target_cost / min_order_size).quantize(Decimal("0.01"), rounding=ROUND_DOWN)
                    final_cost = adjusted_size_dec * price_dec
                    cost_decimals = abs(Decimal(str(final_cost)).as_tuple().exponent)
                    logger.info(f"  📏 Adjusted to minimum size: {adjusted_size_dec} @ {price_dec}")
                
                final_price = float(price_dec)
                final_size = float(adjusted_size_dec)
                final_cost_float = float(final_cost)
                
                logger.info(f"  Order Values (BUY): price={final_price:.2f}, size={final_size:.2f}, cost={final_cost_float:.2f}, cost_decimals={cost_decimals}")
            
            if final_size < 0.01:
                raise OrderError(f"Calculated size too small: {final_size}")

            order_args = OrderArgs(
                price=final_price,
                size=final_size,
                side=side_str,
                token_id=order.token_id
            )
            
            # Blocking calls in async
            signed_order = self.client.create_order(order_args)
            # Use GTC for buys (marketable limit), FOK for sells (immediate exit)
            order_type = OrderType.GTC if order.side == Side.BUY else OrderType.FOK
            resp = self.client.post_order(signed_order, order_type)
            
            if resp and resp.get("success"):
                target = order.market_name or order.token_id
                # Using print as we might not have initialized logger in adapters, but print goes to docker logs
                print(f"✅ [REAL BUY] Placed Order for {target}: {resp.get('orderID')}")
                return resp.get("orderID")
            elif resp and resp.get("orderID"):
                 target = order.market_name or order.token_id
                 print(f"✅ [REAL BUY] Placed Order for {target}: {resp.get('orderID')}")
                 return resp.get("orderID")
            else:
                raise OrderError(f"Order placement failed: {resp}")

        except PolyApiException as e:
            raise APIError(f"Polymarket API Error: {e}")
        except Exception as e:
            raise OrderError(f"Unexpected error placing order: {e}")

    async def get_order_book(self, token_id: str) -> MarketDepth:
        # Try Websocket Cache first
        cached_depth = await self.ws_client.get_order_book(token_id)
        if cached_depth:
            # If we have data, return it.
            # Note: WS might not have 'min_order_size' accurately if not in stream?
            # Our WS client defaults to 0.0. The REST API gives it.
            # If critical, we might need one REST call to get metadata or min_size.
            # For now, we assume user accepts 0.0 or we default to global min (2.0 USD is min order value typically).
            return cached_depth

        # If not in cache, subscribe and fallback to REST
        await self.ws_client.subscribe([token_id])
        return await self._get_order_book_rest(token_id)

    async def _get_order_book_rest(self, token_id: str) -> MarketDepth:
        try:
            # We use a raw request here to ensure we get 'min_order_size' which might 
            # not be exposed by the py-clob-client wrapper depending on version.
            url = f"{self.client.host.rstrip('/')}/book"
            params = {"token_id": token_id}
            
            # Use requests (blocking) - wrapped in async def
            resp = requests.get(url, params=params, timeout=10)
            resp.raise_for_status()
            data = resp.json()
            
            bids_data = data.get("bids", [])
            asks_data = data.get("asks", [])
            min_size = float(data.get("min_order_size", 0.0))
            
            bids = [MarketDepthLevel(price=float(b.get("price")), size=float(b.get("size"))) for b in bids_data]
            asks = [MarketDepthLevel(price=float(a.get("price")), size=float(a.get("size"))) for a in asks_data]
            
            return MarketDepth(bids=bids, asks=asks, min_order_size=min_size)
            
        except Exception as e:
            raise APIError(f"Failed to fetch order book: {e}")

    async def get_market_metadata(self, token_id: str) -> MarketMetadata:
        try:
            # Polymarket Gamma API to get market details by CLOB Token ID
            url = "https://gamma-api.polymarket.com/markets"
            params = {"clob_token_ids": token_id}
            
            resp = requests.get(url, params=params, timeout=10)
            resp.raise_for_status()
            data = resp.json()
            
            if not data or not isinstance(data, list):
                return MarketMetadata(title="Unknown", question="Unknown")
                
            market = data[0]
            
            # Extract category from series (like play.ipynb)
            event = market.get('events', [{}])[0] if market.get('events') else {}
            series = event.get('series', [{}])[0] if event.get('series') else {}
            category = series.get('title', 'Uncategorized')
            
            # Parse outcomes and prices (like play.ipynb)
            import json as json_module
            outcomes_dict = None
            outcomes = []
            try:
                raw_outcomes = market.get('outcomes')
                raw_prices = market.get('outcomePrices')
                
                outcomes = json_module.loads(raw_outcomes) if isinstance(raw_outcomes, str) else (raw_outcomes or [])
                prices = json_module.loads(raw_prices) if isinstance(raw_prices, str) else (raw_prices or [])
                
                if outcomes and prices:
                    outcomes_dict = {outcome: float(price) for outcome, price in zip(outcomes, prices)}
            except (json_module.JSONDecodeError, TypeError, ValueError):
                pass
            
            # Determine which outcome this token ID represents
            queried_outcome = None
            try:
                raw_token_ids = market.get('clobTokenIds')
                token_ids = json_module.loads(raw_token_ids) if isinstance(raw_token_ids, str) else (raw_token_ids or [])
                
                if token_ids and outcomes:
                    for idx, tid in enumerate(token_ids):
                        if tid == token_id and idx < len(outcomes):
                            queried_outcome = outcomes[idx]
                            break
            except (json_module.JSONDecodeError, TypeError, ValueError, IndexError):
                pass
            
            # Format end date
            end_date = None
            raw_end = market.get('endDate')
            if raw_end:
                try:
                    from datetime import datetime
                    dt = datetime.fromisoformat(raw_end.replace('Z', '+00:00'))
                    end_date = dt.strftime('%Y-%m-%d %H:%M UTC')
                except (ValueError, TypeError):
                    end_date = raw_end
            
            # Extract score for sports markets
            score = event.get('score') if event else None
            
            return MarketMetadata(
                title=market.get("title", "Unknown"),
                question=market.get("question", "Unknown"),
                group_name=market.get("groupItemTitle", None),
                category=category,
                status="Closed" if market.get("closed") else "Active",
                volume=float(market.get("volume", 0)) if market.get("volume") else None,
                end_date=end_date,
                outcomes=outcomes_dict,
                score=score,
                queried_outcome=queried_outcome
            )
        except Exception as e:
            # Fallback so we don't crash logging
            return MarketMetadata(title="Error Fetching Metadata", question=str(e))
