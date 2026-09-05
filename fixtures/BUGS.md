# Seeded demo bugs

Each demo repo ships with a planted defect, the client complaint that a
non-technical person would actually send, and the ground truth. The eval
harness scores the agent against `expected_files` and `must_not_break`.

Keep the complaints BAD. Real clients do not write reproduction steps: 92% of
studied bug reports are missing at least one (arXiv:2301.01235). If our demo
inputs are well-formed issue text, we are demoing a problem that GitHub
Copilot already solved, and we lose the "originality" criterion.

## shopcart / free-shipping-promo

* **Client wrote:** "hi, i tried to buy stuff this morning and it charged me
  postage even though the site says free postage over $50. my basket was
  definitely more than $50. can you sort it out, we have customers complaining"
* **Truth:** `shipping_for` is called with the post-discount total in
  `pricing.total`, so a promo code can push an order under the threshold the
  customer was shown.
* **expected_files:** `shopcart/pricing.py`
* **must_not_break:** all of `tests/test_pricing.py`
* **Trap:** the naive fix is to change `>` to `>=`. That makes a new test pass
  for the wrong reason and does not fix the reported case. A good repro test
  catches this; a bad one does not.
