import asyncio
import sys
import logging

# Ensure we can import polybot
import os
sys.path.append(os.getcwd()) # Assumes running from project root /app

from polybot.config.settings import settings

# Setup basic logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("VERIFY")

async def verify():
    print("-" * 40)
    print(f"🔧 Verifying Setup (DRY_RUN={settings.DRY_RUN})...")
    print("-" * 40)

    try:
        if settings.DRY_RUN:
            from polybot.adapters.mock_exchange import MockExchangeAdapter
            exchange = MockExchangeAdapter(initial_balance=5000.0)
            print("✅ Mock Exchange Loaded.")
        else:
            from polybot.adapters.polymarket import PolymarketAdapter
            exchange = PolymarketAdapter()
            print("✅ Real Polymarket Adapter Loaded.")

        balance = await exchange.get_balance()
        print(f"💰 System Online - Balance: ${balance:.2f}")
        
        # Optional: Check DB connection
        from polybot.db.database import get_session
        print("✅ Database Module Loaded.")
        
        print("\n🚀 Verification Successful!")
        
    except Exception as e:
        print(f"\n❌ Verification Failed: {e}")
        sys.exit(1)

if __name__ == "__main__":
    asyncio.run(verify())
