# The five-minute video — script and shot list

Task E5 in `tasks/ENGINEER_E.md`. **Script it, then record. Do not improvise.**

Mode: a **real run against Bedrock** — Claude Haiku 4.5, AWS access keys. Every
number said aloud below is one this repository can produce.

**The deck order is the video order.** Ten slides, Session 3's flow, and you
advance with `→` and never go back. The live demo happens *on* slide 08, which is
the demo slide: you cut to the browser and cut back. There is no jumping.

- **Speech:** 649 words. At a presenting pace (~145 wpm) that is about 4 min 10 s
  of talking inside a 5 min video. Every section has 2–5 seconds of slack, and the
  demo carries ~20 seconds of deliberate silence — the seconds where the agent is
  being watched, not described.

---

## Before you press record

```bash
# 1. Credentials. Sandbox keys expire every 12h — re-paste them first, every time.
#    .env needs AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, AWS_SESSION_TOKEN
#    and AWS_DEFAULT_REGION=us-east-1
make check-bedrock          # must print the model id that answers. Do not skip.

# 2. Backend and UI, two terminals.
LLM_PROVIDER=bedrock make api
make web
```

Then, before recording:

1. **The deck.** Open the artifact, click **Theme** and force **light** — the app
   is light, and cutting between a dark slide and a light app is jarring. Then
   **Present**, then `F11` for fullscreen. `→` forward, `Esc` out.
2. **The app.** `http://localhost:5173`. Confirm the badge names **Claude Haiku
   4.5 (Bedrock)**, not "recorded replies". If it says replay, the keys are not
   loaded — stop and fix it, or you will narrate a real run over a fake one.
3. **Code** tab → Demo repo → **shopcart**. Leave it there; that is the frame you
   cut to at 2:54.
4. Browser at 1440×900, zoom 110%, bookmarks bar hidden, notifications off.
5. **Rehearse the run two or three times for real.** About 1.5 cents each. You are
   checking one thing: *does the fix get accepted?* Read "If the patch is refused"
   at the bottom before you commit to the script below.

Record the slide segments and the demo as **separate clips** and assemble them.
You want that anyway: you can re-take the demo without re-recording the narration.

Record at 1080p, deliver 720p, captions throughout. Check the code is legible
after compression on a 30-second test clip **before** recording all five minutes.

---

## Shot list — straight through, 01 to 10

| Time | Window | Slide | Beat |
|---|---|---|---|
| 0:00 | 14s | **01** Title & team | Who we are, in one line |
| 0:14 | 54s | **02** Problem | The email, read aloud. The 92% |
| 1:08 | 20s | **03** Solution | The differentiator is a refusal |
| 1:28 | 18s | **04** Methodology | How we scoped down |
| 1:46 | 26s | **05** Architecture | Two loops. The graph |
| 2:12 | 20s | **06** Innovation | "Doesn't Copilot already do this?" |
| 2:32 | 22s | **07** Benefits | 0% false-fix — then: watch it |
| 2:54 | 90s | **08** Demo → **cut to browser** | The live run |
| 4:24 | 20s | **09** Roadmap | What's next |
| 4:44 | 16s | **10** Conclusion | The challenge, and thank you |

A real run takes about 35 seconds end to end. Show slide 08 for three seconds,
cut to the browser, click **Run** at roughly 3:12, and the stream lands under
your narration. Cut back to the deck on slide 09 — never back to 08.

---

## The script

### 0:00 · Slide 01

> We're Repro. We take the email a client sends when something breaks, and turn
> it into a test that fails for the reason they described.

**Advance on "described."**

---

### 0:14 · Slide 02

> Here's what that email actually looks like.

*Pause. Read the quote card verbatim, slower than feels natural:*

> "hi, i tried to buy stuff this morning and it charged me postage even though
> the site says free postage over fifty dollars. my basket was definitely more
> than fifty dollars. can you sort it out, we have customers complaining."

*Beat.*

> No steps. No error message. No browser. And one guess in there that's wrong.
>
> That isn't unusual. Ninety-two percent of studied bug reports are missing at
> least one step you need to reproduce them.
>
> So on Monday morning, a solo maintainer at a small agency has to turn that one
> sentence into a test that fails for the reason the client is describing —
> before anyone touches the code. That's the problem we picked.

---

### 1:08 · Slide 03

> Our solution has one differentiator, and it's a refusal.
>
> Repro writes a test that fails for the reported reason *first*, and accepts a
> patch only when that test goes green **and** the existing suite stays green.
> No reproduction, no patch.

---

### 1:28 · Slide 04

> We got here by trying to break our own problem statement — six ways it could
> have been too vague, too broad, or already solved.
>
> It survived five of them. The sixth is the next slide but one.

---

### 1:46 · Slide 05

> Six nodes, and the two red loops are what make this an agent.
>
> Repro loops when the test it just wrote *passes* — because a passing test means
> it did **not** reproduce the bug. Fix loops until both pytest runs come back
> green. Three of the four ways out of this graph end with no patch.

---

### 2:12 · Slide 06

> Now that sixth one. You're about to ask whether Copilot's coding agent already
> does this.
>
> Copilot, Devin and gitagent all start from a well-formed GitHub issue. Writing
> that issue is exactly the work our person cannot get the client to do.

---

### 2:32 · Slide 07

> So what did we measure?
>
> The number that matters is the false-fix rate. Zero percent — not because we got
> lucky on nine cases, but because a function refuses the record.
>
> Let me show you that zero being enforced. Live.

---

### 2:54 · Slide 08 → cut to the browser

**Hold slide 08 for ~3 seconds, then cut.**

**ON SCREEN:** Code tab, shopcart, `pricing.py` open.

> This is the repository it's about to search, and its existing test suite.

**DO:** Click **Use this complaint**. The email appears in the box.

> That's the client's email, exactly as she wrote it.

**DO:** Click **Run**. Switch to the **Run** tab. *(~3:12)*

> This is a real run. Claude Haiku 4.5, on Bedrock, right now.

*As intake and localise stream in:*

> It's pulled out what she said, and marked what she didn't.
>
> It's picked `pricing.py` — because she wrote "postage", and the code says
> "shipping".
>
> Now it's writing a test, and running it.

*Let it run. Say nothing for a few seconds.*

#### The fifteen seconds that matter

*The moment attempt 1 lands green:*

> And the first test it wrote **passed**.
>
> That is not a win. A passing test means it did **not** reproduce the bug — so
> its hypothesis was wrong. And it knows that. Watch: it's revising.

*Do not talk over the retry. Let it appear.*

> Second attempt. **Red.** Now it has proof the bug is real, and proof of what
> it is.

#### The patch

> Now — and only now — will it try a patch. It applies the diff and runs pytest
> twice: the new test, and the whole existing suite.
>
> Both green. That's the zero you saw on the last slide, being enforced. And that
> run cost about one and a half cents.

**DO:** Scroll to the client reply.

> And it closes the loop with the person who complained. In her words. No jargon.
> And marked as a draft — because a person sends it.

**Cut back to the deck, on slide 09.**

---

### 4:24 · Slide 09

> Next is a GitHub App with webhooks, so the reproduction is waiting when the
> maintainer opens their laptop.
>
> And every run already records which file we guessed and which one the bug was
> really in — a labelled dataset, for free.

---

### 4:44 · Slide 10

> Every other agent will hand you a patch. Ask it to show you the test that
> failed first.
>
> If you check one thing of ours — `contracts.py`, line 277.
>
> Thank you.

---

## If the patch is refused

**Read this before you commit to the script above.** The one live Bedrock run we
have on record (`eval/recorded_runs.json`) reproduced the bug and then had all
three patch attempts rejected — it ended `REPRODUCED_NOT_FIXED`. The green-badge
moment is the part of this script the model has to earn, and it may not.

Rehearse it. If two or three live runs land the fix, use the script above. If they
don't, keep **everything** up to "Second attempt. Red." unchanged — the
reproduction is the product, and it works — and swap the two beats after it:

> #### The patch (alternate)
>
> Now it tries a patch. It applies the diff and runs pytest twice: the new test,
> and the existing suite.
>
> **Rejected.** And again. And a third time. So it stops.
>
> That is the false-fix rate staying at zero, in real time. It had a reproduction,
> it had a hypothesis, and it still would not hand you a diff its own evidence
> didn't support. Most agents would have shipped one of those three.

> #### The reply (alternate)
>
> **DO:** Scroll to the client reply.
>
> And it says exactly that to the client. We reproduced your problem, we know
> where it is, we don't have a safe fix yet. No jargon, and no promise the
> evidence doesn't support.

**Do not re-record until you get a lucky green take and present that as typical.**
The refusal take is a weaker demo and a stronger pitch, and it is honest.

---

## Delivery notes

- **The "first test passed" beat is the whole video.** Everything before it is
  setup and everything after is consequence. Slow down there, and never cut it
  for time.
- Narrate the **reasoning**, never the interface. Never say "as you can see here"
  or "this panel shows".
- Slide 07 before the demo is deliberate: you state the number, then the demo
  proves it. Land "let me show you that zero being enforced" and cut immediately.
- If you are running long, cut slide 04 to one sentence. Never cut 06, and never
  cut the failed first attempt.
- **Let the last slide breathe.** It is deliberately under-written: the
  thirty-six-second quickstart is printed on it for the judges to read, so you
  don't have to say it. Land the two lines that are there and stop.
- Read the client email more slowly than feels comfortable. It is the only moment
  where the audience meets the person we built this for.
