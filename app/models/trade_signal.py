from pydantic import BaseModel
from typing import Optional

class TradeSignal(BaseModel):
    action: str
    strategy: str
    confidence: float
    entry: Optional[float] = None
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    quantity: Optional[int] = 1
    reason: str
