# The five-minute video — script and shot list

Task E5 in `tasks/ENGINEER_E.md`. **Script it, then record. Do not improvise.**

Mode: a **real run against Bedrock** — Claude Haiku 4.5, AWS access keys. Every
number said aloud below is one this repository can produce.

- **Speech:** 613 words. At a presenting pace (~145 wpm) that is 4 min 14 s of
  talking inside a 5 min video. Every section below has 2–5 seconds of slack on
  top of the words, and the demo carries ~22 seconds of deliberate silence —
  those are the seconds where the agent is being watched, not described.
  **If you are consistently landing over 5:00, cut slide 10 first.**
- **Slides used on camera:** 01, 02, 03, 07, 08, 05, 10, 11 — in that order.
  Slides 04, 06 and 09 are for the deck a judge reads, not for the video.
  (Slide 09's screenshots are your fallback if the live demo dies.)

---

## Before you press record

```bash
# 1. Credentials. The keys expire every 12h — re-paste them first, every time.
#    .env needs AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, AWS_SESSION_TOKEN
#    and AWS_DEFAULT_REGION=us-east-1
make check-bedrock          # must print the model id that answers. Do not skip.

# 2. Backend and UI, two terminals.
LLM_PROVIDER=bedrock make api
make web
```

Then, in the browser, **before recording**:

1. Open `http://localhost:5173`. Confirm the badge names **Claude Haiku 4.5
   (Bedrock)** and not "recorded replies". If it says replay, the keys are not
   loaded — stop and fix that.
2. **Code** tab → Demo repo → **shopcart**. Leave it on the Code tab. That is
   your opening frame.
3. Browser at 1440×900, zoom 110%, bookmarks bar hidden, notifications off.
4. **Rehearse the run two or three times for real.** It costs about 1.5 cents a
   go. You are checking one thing: *does the fix get accepted?* Read
   "If the patch is refused" at the bottom before you decide which script to use.

Record at 1080p, deliver 720p, captions throughout. Check the code is legible
after compression on a 30-second test clip **before** recording all five minutes.

---

## Shot list

| Time | Window | On screen | Beat |
|---|---|---|---|
| 0:00 | 18s | Slide 01 | Who we are, in one line |
| 0:18 | 52s | Slide 02 | The email, read aloud. The 92% |
| 1:10 | 20s | Slide 03 | The differentiator is a refusal |
| 1:30 | 18s | Slide 07 | "Doesn't Copilot already do this?" |
| 1:48 | 20s | **Browser** | The repo, the complaint, Run |
| 2:08 | 20s | **Browser** | Intake, localise — narrate the reasoning |
| 2:28 | 18s | **Browser** | **Attempt 1 passed. Not a reproduction.** |
| 2:44 | 10s | **Browser** | Attempt 2 is red. Proof |
| 2:54 | 20s | **Browser** | Patch, two green badges |
| 3:14 | 14s | **Browser** | The client reply |
| 3:28 | 24s | Slide 08 | 0% false fix, 1.5 cents |
| 3:52 | 24s | Slide 05 | The graph — how |
| 4:16 | 20s | Slide 10 | Roadmap |
| 4:36 | 24s | Slide 11 | Close |

The demo window is 1:48–3:28, and a real run takes about 35 seconds end to end.
Click **Run** at roughly 2:05 and the stream will land under your narration.

---

## The script

### 0:00 · Slide 01

> We're Repro. We take the email a client sends when something breaks, and we
> turn it into a test that fails for the reason they described. From "it's
> broken" — to a failing test.

**Advance on "failing test."**

---

### 0:18 · Slide 02

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

### 1:10 · Slide 03

> Our solution has one differentiator, and it's a refusal.
>
> Repro writes a test that fails for the reported reason *first*, and accepts a
> patch only when that test goes green **and** the existing suite stays green.
> No reproduction, no patch.

---

### 1:30 · Slide 07

> You're about to ask whether Copilot's coding agent already does this.
>
> Copilot, Devin and gitagent all start from a well-formed GitHub issue. Writing
> that issue is exactly the work our person cannot get the client to do.

**Cut to the browser on the last word.**

---

### 1:48 · Browser — the setup

**ON SCREEN:** Code tab, shopcart, `pricing.py` open.

> This is the repository the agent is about to search — four files, and its
> existing test suite.

**DO:** Click **Use this complaint**. The email appears in the box.

> That's the client's email, exactly as she wrote it.

**DO:** Click **Run**. Switch to the **Run** tab.

> This is a real run. Claude Haiku 4.5, on Bedrock, right now.

---

### 2:08 · Browser — narrate the reasoning, never the UI

*As intake and localise stream in:*

> It's pulled out what she said, and marked what she didn't say.
>
> Now it's guessing where the bug is. It's picked `pricing.py` — because she
> wrote "postage", and the code says "shipping".
>
> It's writing a test, and running it in a sandbox.

*Let it run. Say nothing for a few seconds.*

---

### 2:28 · Browser — **the fifteen seconds that matter**

*The moment attempt 1 lands green:*

> And the first test it wrote **passed**.
>
> That is not a win. A passing test means it did **not** reproduce the bug — so
> its hypothesis was wrong. And it knows that. Watch: it's revising.

*Do not talk over the retry. Let it appear.*

---

### 2:44 · Browser

> Second attempt. **Red.** Now it has proof the bug is real, and proof of what
> it is.

---

### 2:54 · Browser

> Now — and only now — will it try a patch. It applies the diff and runs pytest
> twice: the new test, and the whole existing suite.
>
> Both green. That's the only way a patch is ever accepted here.

---

### 3:14 · Browser

**DO:** Scroll to the client reply.

> And it closes the loop with the person who complained. In her words. No
> jargon. And marked as a draft — because a person sends it.

**Cut to slide 08.**

---

### 3:28 · Slide 08

> The number we care about is the false-fix rate. Zero percent — not because we
> got lucky on nine cases, but because a function refuses the record.
>
> Three of those nine are supposed to end with no patch at all. And that run cost
> about one and a half cents.

---

### 3:52 · Slide 05

> This is how.
>
> Six nodes, and the two red loops are the agent. Repro loops when its test
> passes, because that's evidence it was wrong. Fix loops until both pytest runs
> are green. Three of the four ways out of this graph end with no patch at all.

---

### 4:16 · Slide 10

> Next is a GitHub App with webhooks, so the reproduction is waiting when the
> maintainer opens their laptop.
>
> And every run already records which file we guessed and which one the bug was
> really in — a labelled dataset, for free.

---

### 4:36 · Slide 11

> Every other agent will hand you a patch. Ask it to show you the test that
> failed first.
>
> And if you check one thing — open `contracts.py`, line 277. If the invariant is
> really enforced there, everything else we've told you follows.
>
> Thank you.

---

## If the patch is refused

**Read this before you commit to the script above.** The one live Bedrock run we
have on record (`eval/recorded_runs.json`) reproduced the bug and then had all
three patch attempts rejected — it ended `REPRODUCED_NOT_FIXED`. The green-badge
moment at 2:50 is the part of this script the model has to earn, and it may not.

Rehearse it. If two or three live runs land the fix, use the script above. If
they don't, keep **everything** up to 2:30 unchanged — the reproduction is the
product, and it works — and swap 2:50 and 3:10:

> ### 2:50 (alternate)
>
> Now it tries a patch. It applies the diff and runs pytest twice: the new test,
> and the existing suite.
>
> **Rejected.** And again. And a third time. So it stops.
>
> This is the whole product in one frame. It had a reproduction, it had a
> hypothesis, and it still would not hand you a diff that its own evidence
> didn't support. Most agents would have shipped one of those three.

> ### 3:10 (alternate)
>
> **DO:** Scroll to the client reply.
>
> And it says exactly that to the client. We reproduced your problem, we know
> where it is, we don't have a safe fix yet. No jargon, and no promise the
> evidence doesn't support.

Then at 3:30, add one sentence to slide 08 before the rest:

> You just watched the false-fix rate stay at zero in real time.

**Do not re-record until you get a lucky green take and present that as typical.**
The refusal take is a weaker demo and a stronger pitch, and it is honest.

---

## Delivery notes

- **The 2:28 beat is the whole video.** Everything before it is setup and
  everything after is evidence. Slow down there, and do not cut it for time.
- Narrate the **reasoning**, never the interface. Never say "as you can see here"
  or "this panel shows".
- If you are running long, cut slide 10 to one sentence. Never cut 07 or the
  2:28 beat.
- **Let the last slide breathe.** It is deliberately under-written: the
  thirty-six-second quickstart is printed on it for the judges to read, so you
  do not have to say it. Land the two lines that are there and stop.
- Read the client email more slowly than feels comfortable. It is the only
  moment where the audience is meeting the person we built this for.
