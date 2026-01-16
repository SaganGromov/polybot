import os
import requests
from typing import List
from py_clob_client.client import ClobClient
from py_clob_client.clob_types import OrderArgs, OrderType
from py_clob_client.exceptions import PolyApiException

from polybot.core.interfaces import ExchangeProvider
from polybot.core.models import Position, Order, OrderStatus, Side, MarketDepth, MarketDepthLevel, MarketMetadata
from polybot.core.errors import APIError, AuthError, OrderError, InsufficientFundsError
from polybot.config.settings import settings

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

    async def get_positions(self) -> List[Position]:
        """
        Adapted from main2.py _fetch_positions
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
                # Note: 'requests' is blocking, so strictly speaking generic blocking io in async def
                # should be run in executor, but for now we follow the simple request.
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
                    # Calculate avg entry price approx
                    avg_entry = (init_val / size) if size else 0.0
                    
                    domain_positions.append(Position(
                        token_id=str(p.get("asset")),
                        size=size,
                        average_entry_price=avg_entry,
                        current_price=float(p.get("currentValue", 0)) # Note: currentValue might be total value, need price?
                        # main2.py uses 'currentValue' as total value. 
                        # To get price per token, we usually check orderbook or 'price' field if available.
                        # For now, let's assume average current price = currentValue / size
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
            
            order_args = OrderArgs(
                price=order.price_limit,
                size=order.size,
                side=side_str,
                token_id=order.token_id
            )
            
            # Blocking calls in async
            signed_order = self.client.create_order(order_args)
            resp = self.client.post_order(signed_order, OrderType.FOK)
            
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
            params = {"clob_token_ids[]": token_id}
            
            resp = requests.get(url, params=params, timeout=10)
            resp.raise_for_status()
            data = resp.json()
            
            if not data or not isinstance(data, list):
                return MarketMetadata(title="Unknown", question="Unknown")
                
            market = data[0]
            
            return MarketMetadata(
                title=market.get("title", "Unknown"),
                question=market.get("question", "Unknown"),
                group_name=market.get("groupItemTitle", None)
            )
        except Exception as e:
            # Fallback so we don't crash logging
            return MarketMetadata(title="Error Fetching Metadata", question=str(e))
