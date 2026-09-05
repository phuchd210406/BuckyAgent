"""The suite that already existed. The agent must never break these."""
from shopcart.pricing import Line, apply_promo, shipping_for, total


def test_subtotal():
    assert Line("A", 10.0, 3).subtotal == 30.0


def test_promo_percentage():
    assert apply_promo(100.0, "SAVE10") == 90.0


def test_promo_unknown_code_is_ignored():
    assert apply_promo(100.0, "NOPE") == 100.0


def test_shipping_charged_below_threshold():
    assert shipping_for(20.0) == 4.90


def test_shipping_free_above_threshold():
    assert shipping_for(80.0) == 0.0


def test_total_small_basket():
    assert total([Line("A", 10.0, 2)]) == 24.90
