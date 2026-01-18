
import asyncio
import json
import logging
import websockets
from typing import List, Dict, Set, Optional
from dataclasses import dataclass, field

from polybot.core.models import MarketDepth, MarketDepthLevel
from polybot.core.errors import APIError

logger = logging.getLogger(__name__)

@dataclass
class OrderBookCache:
    token_id: str
    bids: Dict[float, float] = field(default_factory=dict) # price -> size
    asks: Dict[float, float] = field(default_factory=dict) # price -> size
    min_order_size: float = 0.0 # Might not get this from WS?

    def update(self, side: str, updates: List[dict]):
        # updates is list of {"price": "0.50", "size": "100"}
        target = self.bids if side == "buy" else self.asks
        for u in updates:
            try:
                price = float(u.get("price", 0))
                size = float(u.get("size", 0))
                if size == 0:
                    target.pop(price, None)
                else:
                    target[price] = size
            except ValueError:
                continue

    def to_market_depth(self) -> MarketDepth:
        # Sort bids desc, asks asc
        sorted_bids = sorted(self.bids.items(), key=lambda x: x[0], reverse=True)
        sorted_asks = sorted(self.asks.items(), key=lambda x: x[0])
        
        return MarketDepth(
            bids=[MarketDepthLevel(price=p, size=s) for p, s in sorted_bids],
            asks=[MarketDepthLevel(price=p, size=s) for p, s in sorted_asks],
            min_order_size=self.min_order_size
        )

class PolymarketWebsocketClient:
    def __init__(self, url: str = "wss://ws-subscriptions-clob.polymarket.com/ws/market"):
        self.url = url
        self.active_subscriptions: Set[str] = set()
        self.order_books: Dict[str, OrderBookCache] = {}
        self._ws: Optional[websockets.WebSocketClientProtocol] = None
        self._running = False
        self._lock = asyncio.Lock()
        self._ping_task = None
        
    async def start(self):
        self._running = True
        logger.info(f"🔌 Websocket Client Starting... ({self.url})")
        asyncio.create_task(self._connect_loop())

    async def stop(self):
        self._running = False
        if self._ws:
            await self._ws.close()

    async def _connect_loop(self):
        while self._running:
            try:
                async with websockets.connect(self.url) as ws:
                    self._ws = ws
                    logger.info("✅ Websocket Connected!")
                    
                    # Initial Handshake / Resubscribe
                    # The WS requires an initial message with "type": "market" to register the channel.
                    # Even if we have no subs, we might need to send something? Docs imply "assets_ids" is needed.
                    # We'll send what we have, or empty list if that's allowed (or wait for first sub).
                    initial_subs = list(self.active_subscriptions)
                    init_msg = {
                        "assets_ids": initial_subs,
                        "type": "market"
                    }
                    await ws.send(json.dumps(init_msg))
                    logger.info(f"🤝 Websocket Handshake sent ({len(initial_subs)} subs)")
                    
                    # Ping loop
                    self._ping_task = asyncio.create_task(self._ping_loop(ws))
                    
                    await self._listen_loop(ws)

            except Exception as e:
                logger.error(f"❌ Websocket Connection Failed: {e}")
                self._ws = None
                await asyncio.sleep(5) # Backoff
            finally:
                if self._ping_task:
                    self._ping_task.cancel()

    async def _ping_loop(self, ws):
        try:
            while self._running:
                await ws.ping()
                await asyncio.sleep(20) # 20s interval (Polymarket asks for 30s, 20 is safe)
        except Exception:
            pass

    async def _listen_loop(self, ws):
        try:
            async for msg in ws:
                await self._handle_message(msg)
        except websockets.ConnectionClosed:
            logger.warning("Websocket Closed.")

    async def _handle_message(self, msg: str):
        if msg == "PONG":
            return

        try:
            # Format analysis from doc example & common sense
            # Expected items: list of updates or single dict
            try:
                data = json.loads(msg)
            except json.JSONDecodeError:
                # Server sent raw string like "INVALID OPERATION" or "PONG" (handled above)
                logger.warning(f"⚠️ Received raw message: {msg}")
                return
            
            # If it's a list, process each item
            items = data if isinstance(data, list) else [data]
            
            async with self._lock:
                for item in items:
                    event_type = item.get("event_type")
                    if event_type not in ["book", "price_change"]:
                        # Might be just asset_id based structure?
                        # The doc example didn't look like standard event_type.
                        # Let's try to detect if it has "asset_id", "bids", "asks"
                        pass

                    # Based on Polymarket CLOB structure (inferred):
                    # { "asset_id": "...", "bids": [...], "asks": [...], "hash": "..." }
                    asset_id = item.get("asset_id")
                    if not asset_id:
                        continue
                        
                    if asset_id not in self.order_books:
                        self.order_books[asset_id] = OrderBookCache(token_id=asset_id)
                    
                    cache = self.order_books[asset_id]
                    
                    # Check format of bids/asks
                    # Usually: [{"price": "0.50", "size": "100"}, ...]
                    if "bids" in item:
                        cache.update("buy", item["bids"])
                    if "asks" in item:
                        cache.update("sell", item["asks"])
                        
        except Exception as e:
            logger.error(f"Error handling WS message: {e} | Msg sample: {msg[:100]}")

    async def subscribe(self, token_ids: List[str]):
        async with self._lock:
            new_ids = [t for t in token_ids if t not in self.active_subscriptions]
            if not new_ids:
                return

            for t in new_ids:
                self.active_subscriptions.add(t)

            if self._ws:
                await self._subscribe(new_ids)

    async def _subscribe(self, token_ids: List[str]):
        # Dynamic subscription uses "operation": "subscribe"
        msg = {
            "assets_ids": token_ids,
            "operation": "subscribe"
        }
        await self._ws.send(json.dumps(msg))
        logger.info(f"📡 Subscribed to {len(token_ids)} tokens via WS")

    async def get_order_book(self, token_id: str) -> Optional[MarketDepth]:
        async with self._lock:
            cache = self.order_books.get(token_id)
            if cache:
                return cache.to_market_depth()
            return None
