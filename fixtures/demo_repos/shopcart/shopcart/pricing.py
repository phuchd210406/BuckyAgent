"""Cart pricing. Contains DEMO BUG #1 (see fixtures/BUGS.md) — do not fix in main."""
from __future__ import annotations

from dataclasses import dataclass

FREE_SHIPPING_THRESHOLD = 50.00
SHIPPING_FLAT = 4.90


@dataclass
class Line:
    sku: str
    unit_price: float
    qty: int

    @property
    def subtotal(self) -> float:
        return self.unit_price * self.qty


def apply_promo(subtotal: float, code: str | None) -> float:
    """Apply a promotional code to the subtotal."""
    if code is None:
        return subtotal
    if code == "SAVE10":
        return subtotal * 0.90
    if code == "WELCOME5":
        return subtotal - 5.00
    return subtotal


def shipping_for(subtotal: float) -> float:
    """Free shipping once the order is large enough."""
    # DEMO BUG #1: threshold is compared against the DISCOUNTED subtotal by the
    # caller, but the promise on the website is about the pre-discount total.
    # A customer with a 55.00 basket and SAVE10 drops to 49.50 and is charged
    # shipping they were told was free.
    if subtotal > FREE_SHIPPING_THRESHOLD:
        return 0.0
    return SHIPPING_FLAT


def total(lines: list[Line], promo: str | None = None) -> float:
    subtotal = sum(line.subtotal for line in lines)
    discounted = apply_promo(subtotal, promo)
    return round(discounted + shipping_for(discounted), 2)
