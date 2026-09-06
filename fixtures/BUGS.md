# Seeded demo bugs

Each demo repo ships with the client complaint that a non-technical person
would actually send, and the ground truth. Most carry exactly one planted
defect. Three do not, on purpose: two contain no defect at all (the client
misread the feature; the fault is on the client's own network) and one contains
a real defect that the complaint gives you no way to identify. The eval harness
scores the agent against `expected_files` and `must_not_break`.

Every repo is stdlib-only, has no `pyproject.toml` (so the sandbox never tries
to install anything), and its suite is green before the agent touches it. Run
one from a COPY outside this tree -- pytest walks up, finds the project's own
`pyproject.toml` and collects nothing otherwise.

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

## subscriptions / month-end-renewal

* **Client wrote:** "some customers didn't get charged at the end of january
  and finance only spotted it last week. it's not everyone, most people renewed
  fine. someone here reckons it's a leap year thing..."
* **Truth:** `add_months` builds `date(year, month, start.day)` with no clamp,
  so a subscription started on the 29th-31st renews into a shorter month and
  `date()` raises before the renewal is written.
* **expected_files:** `subscriptions/billing.py`
* **must_not_break:** all of `tests/test_billing.py`
* **Trap:** the client's leap-year theory is wrong, and 2026 is not one -- an
  agent that anchors on February misses that April, June, September and
  November break identically. The cheap fix `min(day, 28)` cures the reported
  case and silently moves every month-end customer to the 28th forever.

## helpdesk / missing-ticket

* **Client wrote:** "the ticket is definitely there, i can pull it up if i
  search her reference, but it is not in the list when you click through the
  pages... is the search index out of date or something?"
* **Truth:** `page_slice` computes `end` as an inclusive index and hands it to
  an exclusive slice. Every page is one ticket short and the dropped ticket is
  not carried onto the next page -- it appears on no page at all.
* **expected_files:** `helpdesk/tickets.py`
* **must_not_break:** all of `tests/test_tickets.py`
* **Trap:** the stated diagnosis points at search, which is the one thing that
  works -- it is how she found the ticket. The suite only ever pages a list
  shorter than a single page, which is why it is green.

## expenses / penny-short

* **Client wrote:** "the kitty never quite adds up. we're a few pence short
  most weeks... nobody is stealing it before you ask."
* **Truth:** `split_evenly` uses integer division and throws the remainder
  away, so 1000p between 3 is 333p each and one penny charged to nobody.
* **expected_files:** `expenses/split.py`
* **must_not_break:** all of `tests/test_split.py`
* **Trap:** money is already held in integer pennies, so an agent that
  pattern-matches "rounding bug" to floats or `Decimal` rewrites the arithmetic
  without fixing anything. The remainder must be distributed, not rounded.

## leaderboard / tied-points

* **Client wrote:** "me and dave finished on the same points this week and he's
  shown above me, but when me and adam tied last month i was the one on top. so
  it's not alphabetical and it's not whoever scored first either."
* **Truth:** `rank` sorts ascending then calls `list.reverse()`, which flips the
  alphabetical tie-break along with the points.
* **expected_files:** `leaderboard/standings.py`
* **must_not_break:** all of `tests/test_standings.py`
* **Trap:** `sorted(..., reverse=True)` is not the fix to reach for blindly --
  Python's sort is stable, so it would preserve ties rather than reverse them.
  The fix is a compound key, `(-points, name)`. The suite has no ties in it.
  The two anecdotes are the only evidence, and they are consistent: the
  reporter sorts between "adam" and "dave".

## notekeeper / under-specified

* **Client wrote:** "it's broken, please fix"
* **Truth:** `NoteStore.search` really is case-sensitive, so "invoice" misses a
  note titled "Invoice March" -- but nothing in the complaint says so, or names
  a screen, or describes a symptom.
* **expected_verdict:** `needs_clarification`
* **expected_files:** none -- any patch is a failure here, **including the one
  that happens to be correct**. The right move is to ask what "broken" looked
  like.
* **must_not_break:** all of `tests/test_store.py`

## delivery / working-days  (NOT A BUG)

* **Client wrote:** "ordered something on the friday, the site said 3 days, it
  turned up the wednesday. that's five days not three... my wife says i'm being
  fussy."
* **Truth:** there is no defect. The estimate is in WORKING days, the weekend
  is correctly skipped, and three working days after a Friday is the following
  Wednesday. `test_standard_from_a_friday_lands_on_wednesday` asserts the exact
  behaviour being reported, so the intent is documented, not inferred.
* **expected_verdict:** `not_reproduced`
* **expected_files:** none
* **must_not_break:** all of `tests/test_estimate.py`
* **Trap:** the most important case we have. The complaint is specific,
  quantified and entirely reasonable-sounding, which is what makes it
  dangerous. An agent that reads "the client is upset" as "there is a bug"
  switches to calendar days, breaks four existing tests, and ships a real
  regression to fix an imaginary problem. The client is owed an explanation of
  working days, not a patch.

## dashboard / preview-overflow  (TWO ATTEMPTS)

* **Client wrote:** "the text on the dashboard cards runs off the side and
  covers up the buttons... can you make the boxes wider"
* **Truth:** `summarise` searches FORWARD for the space after `limit`, so the
  word straddling the boundary is kept whole and the preview overruns the card.
* **expected_files:** `dashboard/summary.py`
* **must_not_break:** all of `tests/test_summary.py`, and specifically
  `tests/test_summary.py::test_words_are_never_cut_in_half`
* **Trap:** the obvious fix -- cut at exactly `limit` -- fixes the overflow and
  breaks `test_words_are_never_cut_in_half`, which is a real pre-existing
  requirement and not a bad test. Verified: attempt 1 leaves the suite at
  "1 failed, 5 passed", so the gate must reject it. Attempt 2 has to cut at the
  last boundary BEFORE the limit while still hard-cutting a string with no
  spaces in it; both constraints are already in the suite. The client also asks
  for the wrong fix outright -- the defect is in the truncation, not the CSS.

## statusboard / client-network  (ROOT CAUSE OUTSIDE THE REPO)

* **Client wrote:** "everything has been really slow for me since tuesday...
  nobody else in the office seems to be getting it which is the odd part. we
  did have someone in to redo the wifi on the monday. i'm not being funny but
  it was fine before you did that update last week."
* **Truth:** nothing in this repository can produce a per-user intermittent
  connection failure; it computes uptime and latency from samples it is handed.
  The two facts that matter are buried in asides -- nobody else is affected,
  and their wifi was reworked the day before it started -- while the
  accusation the client leads with is wrong.
* **expected_verdict:** `not_reproduced`
* **expected_files:** none
* **must_not_break:** all of `tests/test_health.py`
* **Trap:** inventing a retry or a timeout change here means altering a repo to
  explain a fault that is not in it. The client reply has to say we could not
  reproduce it, say what we checked, and ask the two questions that would
  confirm it (another network? their phone, off the office wifi?) without
  telling the customer it is their fault.
