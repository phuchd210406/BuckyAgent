# Problem statement

## The statement

> **A solo maintainer at a small software agency, on the Monday morning a client
> emails "the checkout is broken, please fix ASAP", needs a way to turn that one
> sentence into a test that fails for the reason the client is describing — before
> anyone touches the code — because 92% of studied bug reports are missing at least
> one step needed to reproduce them (Johnson et al., "An Empirical Investigation
> into the Reproduction of Bug Reports for Android Apps", arXiv:2301.01235, 2023),
> and developers spend 35–50% of their working time validating and debugging
> (Layman & Zazworka, "The Debugging Mindset", ACM Queue, 2017).**

One person. One moment. One piece of evidence with a source.

## Why this wording and not the obvious one

The obvious statement — *"developers need help fixing bugs"* — fails four of the
six tests in Session 3. This one is built to survive all six.

| Failure mode | How the obvious version fails | How this version answers |
|---|---|---|
| **The Solution in Disguise** | "Developers need an AI agent that fixes bugs" names our build. | No technology appears. It names a person, a Monday morning, and a missing artefact. |
| **The Everyone Problem** | "Developers" is 30 million people. | A solo maintainer at a small agency: no QA team, no reproduction engineer, the client emails *them* directly. |
| **The Missing Because** | Asserted, not evidenced. | Two citations, both with authors and dates, both about the *reproduction* gap specifically. |
| **The Boiling Ocean** | "Fix bugs" is the whole discipline. | One slice: the distance between a complaint and a red test. We are not claiming to fix all bugs. |
| **The Solved Problem** | **This is the real danger.** Copilot coding agent, Devin, Cursor, Sweep and gitagent all fix bugs from issues. | They all *start from a well-formed issue*. Our person does not have one. See below. |
| **The Comfortable Guess** | Written from our imagination. | **ACTION REQUIRED before the deck is final** — see "Evidence we still owe". |

## The Solved Problem test, answered properly

A judge who has seen GitHub Copilot's coding agent will ask this within thirty
seconds. The answer has three parts and all three must be on the slide.

1. **Existing agents begin where our person is stuck.** Copilot's agent, Devin
   and gitagent all take a GitHub issue as input. Writing that issue *is* the
   work our person cannot get the client to do. 92% of real reports are missing
   a reproduction step; someone has to infer them, and today that someone is a
   human on a Monday morning.

2. **Existing agents optimise for a patch. We refuse to emit one without a
   failing test first.** This is not a stylistic preference, it is the safety
   property, and it is enforced in code (`RunRecord.check_invariants`). A recent
   study of LLM-generated bug report summaries found 12.3% contained fabricated
   content (arXiv:2605.24137). A plausible patch for an unreproduced bug is the
   most expensive artefact in software: it closes the ticket and leaves the bug.
   ACM Queue makes exactly this argument about human debugging — the only
   hypothesis worth having is the one that tells you how a regression test
   should behave.

3. **Existing agents serve the developer. We close the loop with the person who
   complained.** Every run ends with a plain-language reply to the client that
   describes what we reproduced *in their words*. The agency's problem is not
   only the bug; it is the four-day email thread.

## Would this problem exist if agentic AI had never been invented?

**Yes.** Agencies have been losing Monday mornings to "it's broken" emails since
before the transformer. That confirms it is a problem statement and not a product
pitch, which is the sharpest of the five pressure-test questions.

## Where agentic AI earns its place (separate slide — this is graded separately)

A fixed workflow cannot do any of the following, and all four appear in the demo:

- **Decide how many questions to ask.** A well-specified report gets zero; a
  one-liner gets one round of at most three. A fixed pipeline either always
  interrogates the client or never does.
- **Revise a wrong hypothesis using evidence from the world.** The repro test
  running green *is an observation that the hypothesis was wrong*. The agent
  re-localises and tries again, up to three times. That is a genuine
  plan → act → observe → replan loop, not a chain of prompts.
- **Choose escalation over guessing.** When three repro attempts fail, the agent
  stops and hands the developer everything it learned instead of shipping a
  patch. Deciding *not* to act is an agentic decision a pipeline cannot make.
- **Verify its own work against reality.** pytest is not an LLM. Red-then-green
  is ground truth, and the agent's judgement is never allowed to override it.

## Evidence we still owe — DO THIS BEFORE THE DECK IS FINAL

Session 3's third pressure-test question is "would that person recognise
themselves?" We cannot answer it from the two citations above, because both are
about bug reports in general, not about *this person*. Budget 45 minutes:

- [ ] Interview two people who live this. Candidates on campus: a senior on an
      Orbital team who shipped to real users, anyone doing freelance web work,
      an NUS SoC alum at a small agency. Ask exactly one question: *"walk me
      through the last time a client told you something was broken — what did
      you do in the first hour?"* Do not describe our idea first.
- [ ] Write down the one sentence each of them says that we did not expect.
- [ ] Rewrite the statement above if either of them contradicts it, and put a
      one-line quote from one of them on slide 2. A real quote from a real
      person is worth more than a third statistic.

Until that box is ticked, the statement is a Comfortable Guess and a judge is
entitled to say so.
