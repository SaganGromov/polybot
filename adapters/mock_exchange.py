from typing import List, Dict
import uuid
from polybot.core.interfaces import ExchangeProvider
from polybot.core.models import Position, Order, MarketDepth, MarketDepthLevel, Side, OrderStatus
from polybot.core.errors import InsufficientFundsError, OrderError

class MockExchangeAdapter(ExchangeProvider):
    def __init__(self, initial_balance: float = 10000.0):
        self.balance = initial_balance
        self._positions: Dict[str, Position] = {} # token_id -> Position
        self.orders: Dict[str, Order] = {}
        print(f"👻 Mock Exchange Initialized with ${initial_balance}")

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
            
            print(f"👻 [MOCK BUY] Bought {order.size} of {order.token_id} @ {order.price_limit}. New Bal: ${self.balance:.2f}")

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
            ]
        )
