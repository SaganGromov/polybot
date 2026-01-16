import asyncio
import logging
import requests
from typing import List, Dict, Optional, Callable, Awaitable
from datetime import datetime
from polybot.core.models import WalletTarget, Side
from polybot.core.events import TradeEvent

logger = logging.getLogger(__name__)

class WhaleMonitor:
    def __init__(self, targets: List[WalletTarget], on_event: Callable[[TradeEvent], Awaitable[None]]):
        self.targets = targets
        self.on_event = on_event
        self.last_timestamps: Dict[str, int] = {t.address: 0 for t in self.targets}
        self.api_url = "https://data-api.polymarket.com/activity" # Base URL based on usage snippets
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
            # We wrap the synchronous requests call in a thread if needed, 
            # but for simplicity/prototype we might call it directly or use run_in_executor.
            # Ideally use aiohttp, but adhering to existing 'requests' usage pattern 
            # while making it async-friendly via to_thread is robust.
            activities = await asyncio.to_thread(self._fetch_activity, target.address)
            
            if not activities:
                return

            # Process from oldest to newest if multiple, but we usually look at the latest
            # The API usually returns newest first.
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
        # Logic adapted from sniper_bot.py
        # URL: {POLY_DATA_API}?user={address}&limit=3&sortBy=timestamp&sortDirection=desc
        # Assuming POLY_DATA_API root is https://data-api.polymarket.com/activity or similar
        # Actually sniper_bot uses: f"{POLY_DATA_API}?user={address}..." where POLY_DATA_API is likely the /activity endpoint
        
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
        # Basic filter could be here or in the event handler. 
        # For a "Monitor", we generally report everything and let the strategy filter.
        
        raw_asset = activity.get('asset', '')
        slug = activity.get('slug', activity.get('marketSlug', 'Unknown'))
        outcome = activity.get('outcome', '')
        if not outcome: 
            outcome = str(raw_asset)

        event = TradeEvent(
            source_wallet_name=target.name,
            source_wallet_address=target.address,
            token_id=str(raw_asset) if str(raw_asset).isdigit() else "", # Logic to ensure it is token ID
            market_slug=slug,
            outcome=outcome,
            side=side,
            usd_size=usd_size,
            timestamp=datetime.utcfromtimestamp(activity.get('timestamp', 0)) if isinstance(activity.get('timestamp'), (int, float)) else datetime.utcnow()
        )
        
        logger.info(f"🔔 Detected {side.value} by {target.name} (${usd_size:.2f})")
        await self.on_event(event)
