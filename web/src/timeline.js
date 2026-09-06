/**
 * Turn the arrival-ordered event log into an arrival-ordered list of cards.
 *
 * Walking the log in order is what keeps a failed repro attempt on screen: a
 * second attempt opens a NEW card rather than reopening the first one. Nothing
 * in here ever mutates a card that is already finished.
 */
export function buildCards(events) {
  const cards = []
  const open = {} // kind -> the card currently awaiting its result

  const openCard = (key, kind, step, data = {}) => {
    const card = { key, kind, step, state: 'running', data }
    cards.push(card)
    open[kind] = card
    return card
  }
  const finish = (kind, data) => {
    const card = open[kind]
    if (!card) return
    card.state = 'done'
    card.data = { ...card.data, ...data }
    delete open[kind]
  }

  for (const event of events) {
    const payload = event.payload || {}
    switch (event.type) {
      case 'node_started': {
        const attempt = payload.attempt_no
        const of = payload.of || 3
        // Cloning a real repository and installing its dependencies happens
        // before the graph starts and is the slowest part of a first run. It is
        // ONE card whose line changes as it goes -- fetching, then copying, then
        // installing -- rather than a card per step, because a person watching
        // wants to know it is still moving, not to read a log.
        if (payload.node === 'prepare') {
          const existing = open.prepare
          if (existing) existing.step = payload.step || existing.step
          else openCard('prepare', 'prepare', payload.step || 'Preparing the repository')
        } else if (open.prepare) {
          // The graph has started, so the sandbox is ready by definition.
          finish('prepare', {})
        }
        if (payload.node === 'intake') openCard('intake', 'intake', 'Reading the complaint')
        if (payload.node === 'localise') {
          openCard('localise', 'localise', 'Searching the code', { hypotheses: [] })
        }
        if (payload.node === 'repro') {
          openCard(
            `repro-${attempt}`, 'repro',
            `Running pytest in sandbox (attempt ${attempt} of ${of})`,
            { attempt_no: attempt, of },
          )
        }
        if (payload.node === 'fix') {
          openCard(
            `fix-${attempt}`, 'fix',
            `Applying the patch, then running both suites (attempt ${attempt} of ${of})`,
            { attempt_no: attempt, of },
          )
        }
        if (payload.node === 'report') openCard('report', 'report', 'Writing the handover')
        break
      }
      case 'hypothesis': {
        const card = open.localise
        if (card) card.data.hypotheses = [...(card.data.hypotheses || []), payload]
        break
      }
      case 'node_finished': {
        // Not `finish`: preparing is several steps and the card closes when the
        // graph moves on, so this only folds in what the step learned.
        if (payload.node === 'prepare' && open.prepare) {
          open.prepare.data = { ...open.prepare.data, ...payload }
        }
        if (payload.node === 'intake') finish('intake', { facts: payload.facts })
        if (payload.node === 'localise') finish('localise', {})
        if (payload.node === 'report') finish('report', {})
        break
      }
      case 'repro_attempt':
        finish('repro', payload)
        break
      case 'fix_attempt':
        finish('fix', payload)
        break
      case 'verdict': {
        // The report card exists only to name the step while it runs; the
        // verdict card is what it becomes, so it does not linger empty.
        finish('report', {})
        const reportAt = cards.findIndex((card) => card.kind === 'report')
        if (reportAt !== -1) cards.splice(reportAt, 1)
        cards.push({ key: 'verdict', kind: 'verdict', state: 'done', data: payload })
        // The reply to the client is its own panel, and it is deliberately the
        // last thing on the page: it is the half of the output that no other
        // bug-fixing agent produces.
        if (payload.handover?.client_reply) {
          cards.push({ key: 'client-reply', kind: 'client_reply', state: 'done', data: payload })
        }
        break
      }
      case 'error':
        // A run that died while cloning leaves the prepare card spinning
        // forever otherwise, which reads as a hang rather than as the failure
        // the very next card explains.
        if (open.prepare) finish('prepare', {})
        cards.push({ key: `error-${cards.length}`, kind: 'error', state: 'done', data: payload })
        break
      default:
        break
    }
  }
  return cards
}

/* Between two nodes the graph is in a router, deciding what to do next. Those
   gaps are short but they are not nothing, and naming them wrongly ("starting
   the run") is worse than a spinner. */
const BETWEEN_STEPS = {
  prepare: () => 'Reading the complaint',
  intake: () => 'Deciding whether the report is clear enough to search on',
  localise: () => 'Picking a hypothesis to test first',
  repro: (card) =>
    card.data.reproduced
      ? 'Reproduced — preparing a patch'
      : 'Revising the hypothesis for another attempt',
  fix: (card) =>
    card.data.accepted
      ? 'Patch accepted — writing the handover'
      : 'Patch rejected — deciding whether to try again',
  report: () => 'Finishing up',
  // A non-terminal error (verification failed) is still followed by a verdict.
  error: () => 'Finishing up',
}

/** The one line that says what is happening right now. Never just a spinner. */
export function currentStep(cards, status) {
  if (status === 'idle') return null
  const running = cards.filter((card) => card.state === 'running').pop()
  if (running) return running.step
  if (status !== 'running') return null
  const last = cards[cards.length - 1]
  if (!last) return 'Starting the run'
  return (BETWEEN_STEPS[last.kind] || (() => 'Deciding what to do next'))(last)
}


/** The most recent bill the stream reported. Every event carries one. */
export function latestUsage(events) {
  for (let i = events.length - 1; i >= 0; i -= 1) {
    const usage = events[i].payload?.usage
    if (usage) return usage
  }
  return null
}
