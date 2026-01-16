import asyncio
import logging
import sys
from polybot.config.settings import settings
from polybot.core.models import WalletTarget
from polybot.services.whale_watcher import WhaleMonitor
from polybot.services.execution import SmartExecutor
from polybot.services.portfolio_manager import PortfolioManager

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("polybot")

async def main():
    logger.info("🚀 Polybot Starting Up...")
    logger.info(f"   Mode: {'DRY RUN (MOCK)' if settings.DRY_RUN else 'LIVE (REAL MONEY)'}")

    # 1. Dependency Injection: Exchange Provider
    if settings.DRY_RUN:
        from polybot.adapters.mock_exchange import MockExchangeAdapter
        exchange = MockExchangeAdapter(initial_balance=10000.0)
    else:
        from polybot.adapters.polymarket import PolymarketAdapter
        try:
            exchange = PolymarketAdapter()
        except Exception as e:
            logger.critical(f"Failed to initialize Real Adapter: {e}")
            return

    # 2. Initialize Services
    executor = SmartExecutor(exchange=exchange)
    manager = PortfolioManager(exchange=exchange, executor=executor)

    # 3. Setup Whale Watcher Targets
    # TODO: Load these from a DB or config file in the future.
    # For now, hardcoded example targets or empty list.
    initial_targets = [
        WalletTarget(address="0x6a72f61820b26b1fe4d956e17b6dc2a1ea3033ee", name="tier1_whale"),
    ]
    
    # WhaleMonitor pushes events to Manager
    whale_watcher = WhaleMonitor(
        targets=initial_targets,
        on_event=manager.on_trade_event
    )

    # 4. Run Loops
    try:
        await manager.start()
        await whale_watcher.start() # This blocks in its loop if awaited directly, need gather
        
        # Since start() methods might just set flags or spawn bg tasks, we check how they are implemented.
        # whale_watcher.start() has a while loop.
        # manager.start() creates a background task and returns.
        
        # So monitoring runs in background, whale loop blocks main.
        # But for robustness, let's gather them properly if both were blocking.
        # whale_watcher.start is blocking. manager.start is non-blocking.
        # So we await whale_watcher.start() effectively keeping the app alive.
        
    except KeyboardInterrupt:
        logger.info("🛑 Shutdown signal received.")
    finally:
        whale_watcher.stop()
        manager.stop()
        logger.info("👋 Goodnight.")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
