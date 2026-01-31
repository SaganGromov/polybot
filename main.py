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
from polybot.services.ai_analysis_service import AIAnalysisService

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
                risk_check = data.get("risk_check_interval_seconds")
                tp_hold_min = data.get("take_profit_hold_min_price")
                sl_hold_min = data.get("stop_loss_hold_min_price")
                
                if sl is not None and tp is not None and min_p is not None:
                    manager.update_strategies(sl, tp, min_p, log_int, max_b, min_pos_val, blacklist, risk_check, tp_hold_min, sl_hold_min)
                elif sl is not None and tp is not None:
                     # Fallback
                    manager.update_strategies(sl, tp, manager.min_share_price, log_int, manager.max_budget, min_pos_val, blacklist, risk_check, tp_hold_min, sl_hold_min)

                # Update AI Config
                ai_config = data.get("ai_analysis", {})
                if ai_config:
                    manager.update_ai_config(
                        enabled=ai_config.get("enabled", False),
                        block_on_negative=ai_config.get("block_on_negative", True),
                        min_confidence=ai_config.get("min_confidence_threshold", 0.6)
                    )
                    # Update AI service max requests if service exists
                    if manager.ai_service:
                        manager.ai_service.update_max_requests(ai_config.get("max_requests", 100))
                        # Update rate limiting config
                        manager.ai_service.update_rate_limit_config(
                            rate_limit_rps=ai_config.get("rate_limit_rps"),
                            max_concurrent_ai=ai_config.get("max_concurrent_ai"),
                            queue_timeout=ai_config.get("queue_timeout")
                        )
                
                # Update Crypto Market Rules config
                crypto_config = data.get("crypto_market_rules", {})
                manager.update_crypto_rules(
                    enabled=crypto_config.get("enabled", False),
                    stop_loss_pct=crypto_config.get("stop_loss_pct", 0.20),
                    take_profit_pct=crypto_config.get("take_profit_pct", 0.45),
                    tp_hold_min_price=crypto_config.get("take_profit_hold_min_price", 0.75),
                    sl_hold_min_price=crypto_config.get("stop_loss_hold_min_price", 0.75)
                )
                if manager.ai_service:
                    manager.ai_service.update_crypto_market_config(
                        enabled=crypto_config.get("enabled", False)
                    )
                
                # Update Sports Filter config (with selective trading)
                sports_config = data.get("sports_filter", {})
                selective_criteria = sports_config.get("selective_criteria", {})
                if manager.ai_service:
                    manager.ai_service.update_sports_filter_config(
                        enabled=sports_config.get("enabled", False),
                        allow_selective=sports_config.get("allow_selective_trades", False),
                        max_days_to_resolution=selective_criteria.get("max_days_to_resolution", 4.0),
                        min_favorite_odds=selective_criteria.get("min_favorite_odds", 0.70)
                    )
                
                # Update Whale Monitor scaling config
                whale_config = data.get("whale_monitor", {})
                if whale_config:
                    whale_watcher.update_scaling_config(
                        batch_size=whale_config.get("batch_size"),
                        batch_delay_ms=whale_config.get("batch_delay_ms"),
                        max_concurrent=whale_config.get("max_concurrent")
                    )


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
    start_risk_check = 10  # Default: check every 10 seconds
    start_tp_hold_min_price = 0.0  # Default: disabled (no hold threshold)
    start_sl_hold_min_price = 0.0  # Default: disabled (no hold threshold)

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
            if "risk_check_interval_seconds" in data:
                start_risk_check = data["risk_check_interval_seconds"]
            if "take_profit_hold_min_price" in data:
                start_tp_hold_min_price = data["take_profit_hold_min_price"]
            if "stop_loss_hold_min_price" in data:
                start_sl_hold_min_price = data["stop_loss_hold_min_price"]
            
            # Load AI config
            ai_config = data.get("ai_analysis", {})
            start_ai_enabled = ai_config.get("enabled", False)
            start_ai_block = ai_config.get("block_on_negative", True)
            start_ai_confidence = ai_config.get("min_confidence_threshold", 0.6)
            start_ai_max_requests = ai_config.get("max_requests", 100)
            
            # Load Crypto Market Rules config
            crypto_config = data.get("crypto_market_rules", {})
            start_crypto_enabled = crypto_config.get("enabled", False)
            start_crypto_sl = crypto_config.get("stop_loss_pct", 0.20)
            start_crypto_tp = crypto_config.get("take_profit_pct", 0.45)
            start_crypto_tp_hold = crypto_config.get("take_profit_hold_min_price", 0.75)
            start_crypto_sl_hold = crypto_config.get("stop_loss_hold_min_price", 0.75)
            
            # Load Sports Filter config (with selective trading)
            sports_config = data.get("sports_filter", {})
            start_sports_enabled = sports_config.get("enabled", False)
            start_sports_selective = sports_config.get("allow_selective_trades", False)
            selective_criteria = sports_config.get("selective_criteria", {})
            start_sports_max_days = selective_criteria.get("max_days_to_resolution", 4.0)
            start_sports_min_odds = selective_criteria.get("min_favorite_odds", 0.70)
                
        except Exception as e:
            logger.error(f"Failed to load initial strategies.json: {e}")
            start_ai_enabled = False
            start_ai_block = True
            start_ai_confidence = 0.6
            start_ai_max_requests = 100
            start_sports_enabled = False
            start_sports_selective = False
            start_sports_max_days = 4.0
            start_sports_min_odds = 0.70
            start_crypto_enabled = False
            start_crypto_sl = 0.20
            start_crypto_tp = 0.45
            start_crypto_tp_hold = 0.75
            start_crypto_sl_hold = 0.75


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

    # 2. Initialize AI Analysis Service
    ai_service = None
    if settings.GEMINI_API_KEY and settings.GEMINI_API_KEY.get_secret_value():
        from polybot.adapters.ai_analyzer import GeminiAnalyzerAdapter
        ai_analyzer = GeminiAnalyzerAdapter()
        
        # Build rate limit config from ai_analysis section
        rate_limit_config = {
            "rate_limit_rps": ai_config.get("rate_limit_rps", 5.0),
            "max_concurrent_ai": ai_config.get("max_concurrent_ai", 10),
            "queue_timeout": ai_config.get("queue_timeout", 120.0)
        }
        
        ai_service = AIAnalysisService(
            analyzer=ai_analyzer, 
            exchange=exchange,
            max_requests=start_ai_max_requests,
            rate_limit_config=rate_limit_config
        )
        logger.info("   🤖 AI Analysis: ENABLED (Gemini)")
    else:
        logger.info("   🤖 AI Analysis: DISABLED (no API key)")

    # 3. Initialize Services
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
        blacklisted_token_ids=start_blacklist,
        ai_service=ai_service,
        risk_check_interval_seconds=start_risk_check,
        take_profit_hold_min_price=start_tp_hold_min_price,
        stop_loss_hold_min_price=start_sl_hold_min_price
    )
    
    # Apply initial AI config
    if ai_service:
        manager.update_ai_config(
            enabled=start_ai_enabled,
            block_on_negative=start_ai_block,
            min_confidence=start_ai_confidence
        )
        # Apply initial Sports Filter config (with selective trading)
        ai_service.update_sports_filter_config(
            enabled=start_sports_enabled,
            allow_selective=start_sports_selective,
            max_days_to_resolution=start_sports_max_days,
            min_favorite_odds=start_sports_min_odds
        )
        # Apply initial Crypto Market Rules config
        ai_service.update_crypto_market_config(enabled=start_crypto_enabled)
    
    # Apply crypto rules to portfolio manager
    manager.update_crypto_rules(
        enabled=start_crypto_enabled,
        stop_loss_pct=start_crypto_sl,
        take_profit_pct=start_crypto_tp,
        tp_hold_min_price=start_crypto_tp_hold,
        sl_hold_min_price=start_crypto_sl_hold
    )

    # 4. Setup Whale Watcher Targets
    # Load whale scaling config
    whale_config = data.get("whale_monitor", {}) if os.path.exists(CONFIG_PATH) else {}
    
    # WhaleMonitor pushes events to Manager
    whale_watcher = WhaleMonitor(
        targets=start_wallets,
        on_event=manager.on_trade_event,
        exchange=exchange,
        batch_size=whale_config.get("batch_size", 50),
        batch_delay_ms=whale_config.get("batch_delay_ms", 100),
        max_concurrent=whale_config.get("max_concurrent", 20)
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
        await whale_watcher.stop()
        manager.stop()
        await exchange.stop()
        logger.info("👋 Goodnight.")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
