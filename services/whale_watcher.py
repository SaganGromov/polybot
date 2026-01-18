import asyncio
import logging
import requests
from typing import List, Dict, Optional, Callable, Awaitable
from datetime import datetime
from polybot.core.models import WalletTarget, Side
from polybot.core.events import TradeEvent
from polybot.core.interfaces import ExchangeProvider

logger = logging.getLogger(__name__)

class WhaleMonitor:
    def __init__(self, targets: List[WalletTarget], on_event: Callable[[TradeEvent], Awaitable[None]], exchange: Optional[ExchangeProvider] = None):
        self.targets = targets
        self.on_event = on_event
        self.exchange = exchange  # Optional exchange for fetching market metadata
        self.last_timestamps: Dict[str, int] = {t.address: 0 for t in self.targets}
        self.api_url = "https://data-api.polymarket.com/activity"
        self._running = False

    def update_targets(self, new_targets: List[WalletTarget]):
        """Updates the list of monitored wallets dynamically."""
        self.targets = new_targets
        logger.info(f"🔄 WhaleMonitor updated: Now watching {len(self.targets)} wallets.")

    async def start(self):
        self._running = True
        logger.info(f"🐳 Whale Monitor started. Watching {len(self.targets)} wallets.")
        while self._running:
            try:
                await self._poll_all()
            except Exception as e:
                logger.error(f"Error in main polling loop: {e}")
            
            await asyncio.sleep(3) # Polling interval

    def stop(self):
        self._running = False

    async def _poll_all(self):
        # Create concurrent tasks for all targets
        tasks = [self._check_wallet(target) for target in self.targets]
        await asyncio.gather(*tasks)

    async def _check_wallet(self, target: WalletTarget):
        try:
            activities = await asyncio.to_thread(self._fetch_activity, target.address)
            
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

    def _fetch_activity(self, address: str) -> List[Dict]:
        url = "https://data-api.polymarket.com/activity"
        params = {
            "user": address,
            "limit": "3",
            "sortBy": "timestamp",
            "sortDirection": "desc"
        }
        try:
            resp = requests.get(url, params=params, timeout=5)
            resp.raise_for_status()
            return resp.json()
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
