# 🤖 Polybot

**A consolidated, Hexagonal Architecture microservice for Copy-Trading on Polymarket.**

This system integrates whale mirroring, automatic profit-taking, stop-loss protection, and direct execution on the Polymarket CLOB (Central Limit Order Book) via a strict strict separation of concerns.

---

## 🏛️ Architecture

The project follows **Hexagonal Architecture (Ports & Adapters)** to decouple business logic from external systems.

### **1. Core (Domain Layer)**
*   **Location:** `polybot/core/`
*   **Purpose:** Defines the "Langauge" of the bot using pure Python models (Pydantic) and Interfaces.
*   **Key Files:**
    *   `models.py`: Defines `Position`, `Order`, `WalletTarget`, etc.
    *   `interfaces.py`: Defines `ExchangeProvider(ABC)`.
    *   `events.py`: Defines `TradeEvent`.
    *   `errors.py`: Custom exception hierarchy (`ExchangeError`, `APIError`).

### **2. Adapters (Infrastructure Layer)**
*   **Location:** `polybot/adapters/`
*   **Purpose:** Implements the interfaces defined in Core to talk to the real world.
*   **Key Files:**
    *   `polymarket.py`: The **Real** Money adapter using `py-clob-client`. Derived from the Core `ExchangeProvider`.
    *   `mock_exchange.py`: The **Paper Trading** adapter. Simulates fills, balances, and PnL in-memory.

### **3. Services (Application Layer)**
*   **Location:** `polybot/services/`
*   **Purpose:** Orchestrates logic using the Core models.
    *   **`whale_watcher.py`**:
        *   **Role:** The "Eyes".
        *   **Behavior:** Polls the Polymarket Data API for target wallet activity using `asyncio.gather` for concurrency.
        *   **Output:** Emits `TradeEvent` to the `PortfolioManager`.
    *   **`execution.py` (`SmartExecutor`)**:
        *   **Role:** The "Hands".
        *   **Behavior:** Handles complex execution logic like **Drip Selling** (breaking large orders into chunks to preserve price).
        *   **Logic:** Checks orderbook liquidity -> Calculates safe sell chunk -> Executes -> Waits -> Repeats.
    *   **`portfolio_manager.py`**:
        *   **Role:** The "Brain".
        *   **Behavior:**
            1.  Receives `TradeEvent` from Watcher.
            2.  Decides whether to Mirror (Budget checks, Risk checks).
            3.  Runs a **Background Risk Loop** every 60s to check ROI.
            4.  Triggers Stop-Loss or Take-Profit via `SmartExecutor` if thresholds are met.

### **4. Config & DB**
*   **`config/settings.py`**: Pydantic-based configuration loading from `.env`.
*   **`db/schemas.py`**: SQLModel database tables (`TradeHistory`, `ActivePosition`).
*   **`main.py`**: The entry point. Wires everything together using Dependency Injection.

---

## 🛠️ Setup & Installation

### **Prerequisites**
*   Docker & Docker Compose

### **1. Configuration**
Create a `.env` file in the `polybot/` directory:

```ini
# --- SECRETS ---
# Your Polygon Wallet Private Key (Required for signing orders)
WALLET_PRIVATE_KEY=0xYOUR_PRIVATE_KEY_HERE

# The Proxy Address for your Polymarket account (Required for CLOB)
PROXY_ADDRESS=0xYOUR_PROXY_ADDRESS_HERE

# --- DATABASE ---
DATABASE_URL=postgresql+asyncpg://user:password@db:5432/polybot

# --- MODE ---
# Set to 'True' for Paper Trading (Mock), 'False' for Real Money
DRY_RUN=True 
```

### **2. running the Bot**
Use Docker Compose to build and start the services (Bot + Postgres).

```bash
# Build and Run in Detached Mode
docker-compose up --build -d

# View Logs
docker-compose logs -f bot
```

### **3. Verification**
Run the built-in verification script to test connectivity without waiting for market events.

```bash
docker-compose exec bot python polybot/scripts/verify_setup.py
```

---

## 🐛 Troubleshooting

### **Permission Denied (Docker)**
If you see `connect: permission denied` for `/var/run/docker.sock`:
*   **Fix:** Run with `sudo docker-compose ...` or add your user to the docker group.

### **API Errors (400 Bad Request)**
*   **Issue:** `invalid amounts ... max accuracy of 2 decimals`.
*   **Fix:** The bot auto-rounds sizes to 2 decimals. If you see this, ensure `portfolio_manager.py` is using `math.floor` with 2 decimal precision.

### **Empty Logs**
*   **Issue:** No output after "Starting...".
*   **Reason:** Python output buffering.
*   **Fix:** Ensure `ENV PYTHONUNBUFFERED=1` is in your `Dockerfile`.

---

## 🧪 Development Workflow

To add a new feature:
1.  **Define Interface:** Update `core/interfaces.py` if you need new Exchange capabilities.
2.  **Update Adapters:** Implement the new method in `polymarket.py` AND `mock_exchange.py`.
3.  **Implement Logic:** Write the business logic in `services/`.
4.  **Test:** Set `DRY_RUN=True` in `.env` and verify behavior in the logs.

---
**Disclaimer:** Use at your own risk. This bot executes real financial transactions when `DRY_RUN=False`.
