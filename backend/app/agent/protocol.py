"""Wire protocol between the agent and the client UI."""

from typing import Any, Literal

from pydantic import BaseModel, Field

UIActionType = Literal[
    "navigate",       # payload: {path}
    "show_products",  # payload: {title, items:[{ref, product_id, variant_id, name, label, price, ...}]}
    "show_orders",    # payload: {title, orders:[...]}
    "refresh_cart",   # payload: {cart}
    "confirm",        # payload: {token, action, title, details, expires_at}
    "clarify",        # payload: {question, options:[{label, value}]}
    "toast",          # payload: {level, text}
]


class UIAction(BaseModel):
    type: UIActionType
    payload: dict[str, Any] = Field(default_factory=dict)


class ClientContext(BaseModel):
    route: str = "/"


class TurnRequest(BaseModel):
    user_id: int = 1
    session_id: str
    turn_id: str  # client-generated; retries of the same submission reuse it (idempotency)
    message: str = Field(min_length=1, max_length=500)
    context: ClientContext = Field(default_factory=ClientContext)


class ConfirmRequest(BaseModel):
    user_id: int = 1
    session_id: str
    token: str
    approve: bool


class TurnResult(BaseModel):
    trace_id: str
    reply: str
    ui_actions: list[UIAction]
    route: str
    latency_ms: float
    steps: list[dict]
