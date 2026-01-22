import asyncio
import logging
import httpx
from typing import List, Dict, Optional, Callable, Awaitable
from datetime import datetime
from polybot.core.models import WalletTarget, Side
from polybot.core.events import TradeEvent
from polybot.core.interfaces import ExchangeProvider

logger = logging.getLogger(__name__)


class WhaleMonitor:
    def __init__(
        self, 
        targets: List[WalletTarget], 
        on_event: Callable[[TradeEvent], Awaitable[None]], 
        exchange: Optional[ExchangeProvider] = None,
        batch_size: int = 50,
        batch_delay_ms: int = 100,
        max_concurrent: int = 20
    ):
        self.targets = targets
        self.on_event = on_event
        self.exchange = exchange
        self.last_timestamps: Dict[str, int] = {t.address: 0 for t in self.targets}
        self.api_url = "https://data-api.polymarket.com/activity"
        self._running = False
        
        # Scaling configuration
        self.batch_size = batch_size
        self.batch_delay_ms = batch_delay_ms
        self._semaphore = asyncio.Semaphore(max_concurrent)
        
        # Connection pooling with httpx
        self._http_client: Optional[httpx.AsyncClient] = None
        
        logger.info(f"🐳 WhaleMonitor initialized: batch_size={batch_size}, batch_delay={batch_delay_ms}ms, max_concurrent={max_concurrent}")

    def update_targets(self, new_targets: List[WalletTarget]):
        """Updates the list of monitored wallets dynamically."""
        # Preserve existing timestamps for wallets we already know
        old_timestamps = self.last_timestamps.copy()
        self.last_timestamps = {}
        for t in new_targets:
            self.last_timestamps[t.address] = old_timestamps.get(t.address, 0)
        self.targets = new_targets
        logger.info(f"🔄 WhaleMonitor updated: Now watching {len(self.targets)} wallets.")

    def update_scaling_config(self, batch_size: int = None, batch_delay_ms: int = None, max_concurrent: int = None):
        """Update scaling configuration dynamically."""
        if batch_size is not None:
            self.batch_size = batch_size
        if batch_delay_ms is not None:
            self.batch_delay_ms = batch_delay_ms
        if max_concurrent is not None:
            self._semaphore = asyncio.Semaphore(max_concurrent)
        logger.info(f"🐳 WhaleMonitor scaling updated: batch_size={self.batch_size}, batch_delay={self.batch_delay_ms}ms")

    async def start(self):
        self._running = True
        # Initialize HTTP client with connection pooling
        self._http_client = httpx.AsyncClient(
            timeout=httpx.Timeout(5.0, connect=2.0),
            limits=httpx.Limits(max_connections=100, max_keepalive_connections=50)
        )
        logger.info(f"🐳 Whale Monitor started. Watching {len(self.targets)} wallets.")
        while self._running:
            try:
                await self._poll_all_batched()
            except Exception as e:
                logger.error(f"Error in main polling loop: {e}")
            
            await asyncio.sleep(3)  # Polling interval

    async def stop(self):
        self._running = False
        if self._http_client:
            await self._http_client.aclose()
            self._http_client = None

    async def _poll_all_batched(self):
        """Poll wallets in batches to avoid API overload."""
        targets = self.targets.copy()  # Snapshot to avoid mutation during iteration
        
        for i in range(0, len(targets), self.batch_size):
            batch = targets[i:i + self.batch_size]
            
            # Process batch with concurrency limit
            tasks = [self._check_wallet_throttled(target) for target in batch]
            await asyncio.gather(*tasks, return_exceptions=True)
            
            # Delay between batches
            if i + self.batch_size < len(targets):
                await asyncio.sleep(self.batch_delay_ms / 1000.0)

    async def _check_wallet_throttled(self, target: WalletTarget):
        """Check wallet with semaphore throttling."""
        async with self._semaphore:
            await self._check_wallet(target)

    async def _check_wallet(self, target: WalletTarget):
        try:
            activities = await self._fetch_activity_async(target.address)
            
            if not activities:
                return

            newest = activities[0]
            new_ts = newest.get('timestamp')
            
            if not new_ts: 
                return

            last_ts = self.last_timestamps.get(target.address, 0)

            # Initialize timestamp if first run
            if last_ts == 0:
                self.last_timestamps[target.address] = new_ts
                return

            if new_ts > last_ts:
                self.last_timestamps[target.address] = new_ts
                await self._process_activity(target, newest)

        except Exception as e:
            logger.warning(f"Failed to check wallet {target.name}: {e}")

    async def _fetch_activity_async(self, address: str) -> List[Dict]:
        """Fetch activity using async httpx with connection pooling."""
        params = {
            "user": address,
            "limit": "3",
            "sortBy": "timestamp",
            "sortDirection": "desc"
        }
        try:
            if self._http_client:
                resp = await self._http_client.get(self.api_url, params=params)
                resp.raise_for_status()
                return resp.json()
            return []
        except Exception:
            return []


    async def _process_activity(self, target: WalletTarget, activity: Dict):
        act_type = activity.get('type', '').upper()
        
        # We only care about explicit trades or matches
        if act_type not in ["TRADE", "MATCH"]:
            return

        side_str = activity.get('side', '').upper()
        
        # Map to Domain Side
        try:
            side = Side(side_str)
        except ValueError:
            return # Unknown side

        # Extract details
        usd_size = float(activity.get('usdcSize', 0))
        raw_asset = activity.get('asset', '')
        slug = activity.get('slug', activity.get('marketSlug', 'Unknown'))
        outcome = activity.get('outcome', '')
        price = float(activity.get('price', 0))
        
        if not outcome: 
            outcome = str(raw_asset)

        # Fetch rich market metadata if exchange is available
        market_question = "Unknown"
        market_category = ""
        market_status = ""
        market_volume = None
        market_end_date = None
        
        token_id = str(raw_asset) if str(raw_asset).isdigit() else ""
        
        if self.exchange and token_id:
            try:
                meta = await self.exchange.get_market_metadata(token_id)
                market_question = meta.question or "Unknown"
                market_category = meta.category or ""
                market_status = meta.status or ""
                market_volume = meta.volume
                market_end_date = meta.end_date
            except Exception as e:
                logger.debug(f"Failed to fetch metadata for {token_id}: {e}")
        
        # Rich logging with all relevant info
        side_emoji = "📈" if side == Side.BUY else "📉"
        logger.info(f"{'='*60}")
        logger.info(f"{side_emoji} WHALE {side.value} DETECTED")
        logger.info(f"   Trader: {target.name}")
        logger.info(f"   Q: {market_question}")
        logger.info(f" Token ID: {token_id}")
        if market_category or market_status:
            logger.info(f"   [{market_category or 'Uncategorized'} | {market_status or 'Unknown'}]")
        if market_volume:
            logger.info(f"   Volume: ${market_volume:,.2f} | Ends: {market_end_date or 'N/A'}")
        logger.info(f"   💰 Amount: ${usd_size:.2f} | Outcome: {outcome} @ {price:.3f}")
        logger.info(f"{'='*60}")

        event = TradeEvent(
            source_wallet_name=target.name,
            source_wallet_address=target.address,
            token_id=token_id,
            market_slug=slug,
            outcome=outcome,
            side=side,
            usd_size=usd_size,
            timestamp=datetime.utcfromtimestamp(activity.get('timestamp', 0)) if isinstance(activity.get('timestamp'), (int, float)) else datetime.utcnow()
        )
        
        await self.on_event(event)
