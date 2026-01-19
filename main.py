import asyncio
import logging
import json
import os
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

CONFIG_PATH = "polybot/config/strategies.json"

async def watch_config(whale_watcher: WhaleMonitor, manager: PortfolioManager):
    """Polls strategies.json for changes and updates services."""
    last_mtime = 0
    
    # Initial load if exists
    if os.path.exists(CONFIG_PATH):
        last_mtime = os.path.getmtime(CONFIG_PATH)
        # We already loaded via settings initially, or maybe we should force load here?
        # Let's rely on settings for startup, but explicit load here is safer to ensure sync.
        pass

    logger.info(f"👀 Config Watcher started. Monitoring {CONFIG_PATH}")

    while True:
        await asyncio.sleep(5)
        try:
            if not os.path.exists(CONFIG_PATH):
                continue

            current_mtime = os.path.getmtime(CONFIG_PATH)
            if current_mtime > last_mtime:
                logger.info("🔄 Configuration Reloaded from strategies.json")
                last_mtime = current_mtime
                
                with open(CONFIG_PATH, 'r') as f:
                    data = json.load(f)

                # Update Wallets
                if "watched_wallets" in data:
                    new_targets = [WalletTarget(**w) for w in data["watched_wallets"]]
                    whale_watcher.update_targets(new_targets)

                # Update Risk Params
                sl = data.get("stop_loss_pct")
                tp = data.get("take_profit_pct")
                min_p = data.get("min_share_price")
                log_int = data.get("portfolio_log_interval_minutes", 60)
                max_b = data.get("max_budget", 100.0)
                min_pos_val = data.get("min_position_value", 0.03)
                blacklist = data.get("blacklisted_token_ids", [])
                
                if sl is not None and tp is not None and min_p is not None:
                    manager.update_strategies(sl, tp, min_p, log_int, max_b, min_pos_val, blacklist)
                elif sl is not None and tp is not None:
                     # Fallback
                    manager.update_strategies(sl, tp, manager.min_share_price, log_int, manager.max_budget, min_pos_val, blacklist)

        except Exception as e:
            logger.error(f"Error re-loading config: {e}")

async def main():
    logger.info("🚀 Polybot Starting Up...")
    logger.info(f"   Mode: {'DRY RUN (MOCK)' if settings.DRY_RUN else 'LIVE (REAL MONEY)'}")

    # 0. Load Dynamic Strategies
    # We load this synchronously at startup to ensure services are initialized with correct targets/risk
    # Defaults (if json missing)
    start_wallets = []
    start_sl = 0.20
    start_tp = 0.90
    start_min_price = 0.19
    start_log_interval = 60
    start_min_pos_value = 0.03
    start_blacklist = []

    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, 'r') as f:
                data = json.load(f)
                
            if "watched_wallets" in data:
                start_wallets = [WalletTarget(**w) for w in data["watched_wallets"]]
                logger.info(f"   Loaded {len(start_wallets)} wallets from strategies.json")
            
            if "stop_loss_pct" in data:
                start_sl = data["stop_loss_pct"]
            if "take_profit_pct" in data:
                start_tp = data["take_profit_pct"]
            if "min_share_price" in data:
                start_min_price = data["min_share_price"]
            if "portfolio_log_interval_minutes" in data:
                start_log_interval = data["portfolio_log_interval_minutes"]
            if "max_budget" in data:
                start_max_budget = data["max_budget"]
            if "min_position_value" in data:
                start_min_pos_value = data["min_position_value"]
            if "blacklisted_token_ids" in data:
                start_blacklist = data["blacklisted_token_ids"]
                
        except Exception as e:
            logger.error(f"Failed to load initial strategies.json: {e}")


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
    manager = PortfolioManager(
        exchange=exchange,
        executor=executor,
        stop_loss_pct=start_sl,
        take_profit_pct=start_tp,
        min_share_price=start_min_price,
        log_interval_minutes=start_log_interval,
        max_budget=start_max_budget,
        min_position_value=start_min_pos_value,
        blacklisted_token_ids=start_blacklist
    )

    # 3. Setup Whale Watcher Targets
    # Loaded from settings
    
    # WhaleMonitor pushes events to Manager
    whale_watcher = WhaleMonitor(
        targets=start_wallets,
        on_event=manager.on_trade_event,
        exchange=exchange  # Pass exchange for rich metadata logging
    )

        # 4. Run Loops
    try:
        # Start background config watcher
        asyncio.create_task(watch_config(whale_watcher, manager))
        
        await exchange.start()
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
        await exchange.stop()
        logger.info("👋 Goodnight.")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
