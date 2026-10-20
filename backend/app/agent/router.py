"""Fast path: unambiguous single-intent navigation commands skip the LLM entirely.
Anything compound or open-ended falls through to the agent loop."""

import re

_NAV = r"(?:(?:please\s+)?(?:go to|go|open|show(?: me)?|take me to|take me|view|see)\s+)?(?:my\s+|the\s+)?"
RULES: list[tuple[re.Pattern, str, dict]] = [
    (re.compile(rf"^{_NAV}(cart|basket|bag)$"), "view_cart", {}),
    (re.compile(r"^(whats|what is|what's) in (my|the) (cart|basket|bag)$"), "view_cart", {}),
    (re.compile(rf"^{_NAV}(checkout|check out)( page)?$"), "navigate", {"page": "checkout"}),
    (re.compile(rf"^{_NAV}(orders|order history|past orders|previous orders)$"), "navigate", {"page": "orders"}),
    (re.compile(rf"^{_NAV}(home|homepage|home page|start)$"), "navigate", {"page": "home"}),
    (re.compile(rf"^{_NAV}(reminders|my reminders)$"), "navigate", {"page": "reminders"}),
    # Safe to route deterministically: place_order only ever creates a confirmation card.
    (re.compile(r"^(please\s+)?(place|confirm|submit|complete)( my| the)? order( now)?$"
                r"|^(pay|order|checkout and pay)( now)?$"
                r"|^buy (it|this|everything|all)? ?now\b.*$"),
     "place_order", {}),
    (re.compile(r"^(please\s+)?(empty|clear|reset)( my| the)? (cart|basket)$"), "clear_cart", {}),
]


def route(message: str) -> tuple[str, dict] | None:
    m = re.sub(r"[^\w\s]", "", message.lower()).strip()
    m = re.sub(r"\s+", " ", m)
    for pattern, tool, args in RULES:
        if pattern.match(m):
            return tool, dict(args)
    return None
