from typing import List, Dict
import uuid
import json
import os
from polybot.core.interfaces import ExchangeProvider
from polybot.core.models import Position, Order, OrderStatus, Side, MarketDepth, MarketDepthLevel, MarketMetadata
from polybot.core.errors import OrderError, InsufficientFundsError, OrderError

class MockExchangeAdapter(ExchangeProvider):
    def __init__(self, initial_balance: float = 10000.0):
        self.state_file = "polybot/config/mock_state.json"
        self.balance = initial_balance
        self._positions: Dict[str, Position] = {} # Map token_id -> Position
        self._orders: Dict[str, Order] = {}
        self._load_state()

    def _load_state(self):
        if os.path.exists(self.state_file):
            try:
                with open(self.state_file, 'r') as f:
                    data = json.load(f)
                    self.balance = data.get("balance", self.balance)
                    
                    # Rehydrate positions
                    pos_data = data.get("positions", [])
                    for p_raw in pos_data:
                        pos = Position(**p_raw)
                        self._positions[pos.token_id] = pos
                        
                print(f"👻 Loaded Mock State. Bal: ${self.balance:.2f}, Pos: {len(self._positions)}")
            except Exception as e:
                print(f"⚠️ Failed to load mock state: {e}")
        else:
            print(f"👻 Mock Exchange Initialized with ${self.balance}")


    def _save_state(self):
        try:
            data = {
                "balance": self.balance,
                "positions": [p.dict() for p in self._positions.values()]
            }
            os.makedirs(os.path.dirname(self.state_file), exist_ok=True)
            with open(self.state_file, 'w') as f:
                json.dump(data, f, indent=2)
            print(f"👻 Mock State Saved. Bal: ${self.balance:.2f}, Pos: {len(self._positions)}")
        except Exception as e:
            print(f"⚠️ Failed to save mock state: {e}")

    async def get_balance(self) -> float:
        return self.balance

    async def get_positions(self) -> List[Position]:
        return list(self._positions.values())

    async def place_order(self, order: Order) -> str:
        cost = order.size * order.price_limit
        
        if order.side == Side.BUY:
            if cost > self.balance:
                raise InsufficientFundsError(f"Mock Insufficient Funds: Have ${self.balance}, need ${cost}")
            
            self.balance -= cost
            
            # Add or update position
            if order.token_id in self._positions:
                pos = self._positions[order.token_id]
                new_size = pos.size + order.size
                # Weighted average entry
                total_value = (pos.size * pos.average_entry_price) + cost
                new_avg = total_value / new_size
                pos.size = new_size
                pos.average_entry_price = new_avg
            else:
                self._positions[order.token_id] = Position(
                    token_id=order.token_id,
                    size=order.size,
                    average_entry_price=order.price_limit,
                    current_price=order.price_limit 
                )
            
            buy_target = order.market_name or order.token_id
            print(f"👻 [MOCK BUY] Bought {order.size} of {buy_target} @ {order.price_limit}. New Bal: ${self.balance:.2f}")
            self._save_state()

        elif order.side == Side.SELL:
            pos = self._positions.get(order.token_id)
            if not pos or pos.size < order.size:
                raise OrderError(f"Mock Sell Failed: Not enough shares. Have {pos.size if pos else 0}")
            
            pos.size -= order.size
            if pos.size <= 0:
                del self._positions[order.token_id]
            
            revenue = cost
            self.balance += revenue
            print(f"👻 [MOCK SELL] Sold {order.size} of {order.token_id} @ {order.price_limit}. New Bal: ${self.balance:.2f}")

        # Simulate Order ID
        order_id = f"mock-{uuid.uuid4()}"
        order.order_id = order_id
        order.status = OrderStatus.FILLED
        self.orders[order_id] = order
        return order_id

    async def get_order_book(self, token_id: str) -> MarketDepth:
        # Return a dummy realistic orderbook
        return MarketDepth(
            bids=[
                MarketDepthLevel(price=0.50, size=1000),
                MarketDepthLevel(price=0.49, size=2000)
            ],
            asks=[
                MarketDepthLevel(price=0.51, size=1000),
                MarketDepthLevel(price=0.52, size=2000)
            ],
            min_order_size=5.0
        )

    async def get_market_metadata(self, token_id: str) -> MarketMetadata:
        return MarketMetadata(
            title="Mock Market Event", 
            question="Will this mock event happen?",
            group_name="Yes"
        )
