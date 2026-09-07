// Drive the Repro UI in headless Chrome over CDP and capture slide 8's three
// moments. No dependencies: Node 24 ships a global WebSocket.
import { writeFileSync } from 'node:fs'

const PORT = 9333, OUT = process.argv[2], URL_ = 'http://localhost:5199/'
const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

const list = await (async () => {
  for (let i = 0; i < 40; i++) {
    try { return await fetch(`http://127.0.0.1:${PORT}/json/list`).then((r) => r.json()) }
    catch { await sleep(500) }
  }
  throw new Error('devtools never came up')
})()
const page = list.find((t) => t.type === 'page')
const ws = new WebSocket(page.webSocketDebuggerUrl)
await new Promise((r) => (ws.onopen = r))

let id = 0
const pending = new Map()
ws.onmessage = (m) => {
  const msg = JSON.parse(m.data)
  if (msg.id && pending.has(msg.id)) { pending.get(msg.id)(msg); pending.delete(msg.id) }
}
const send = (method, params = {}) =>
  new Promise((res, rej) => {
    const n = ++id
    pending.set(n, (msg) => (msg.error ? rej(new Error(method + ': ' + JSON.stringify(msg.error))) : res(msg.result)))
    ws.send(JSON.stringify({ id: n, method, params }))
  })

const evaluate = async (expr) => {
  const r = await send('Runtime.evaluate', { expression: expr, awaitPromise: true, returnByValue: true })
  if (r.exceptionDetails) throw new Error(expr.slice(0, 60) + ' -> ' + r.exceptionDetails.text)
  return r.result.value
}
const shot = async (name) => {
  const { data } = await send('Page.captureScreenshot', { format: 'png', captureBeyondViewport: false })
  writeFileSync(`${OUT}/${name}.png`, Buffer.from(data, 'base64'))
  console.log('captured', name)
}
// Poll the DOM until `test` (a JS boolean expression) is true.
const until = async (label, test, timeoutMs = 120000) => {
  const t0 = Date.now()
  while (Date.now() - t0 < timeoutMs) {
    if (await evaluate(`!!(${test})`)) return
    await sleep(250)
  }
  throw new Error('timed out waiting for ' + label)
}

await send('Page.enable')
await send('Runtime.enable')
await send('Emulation.setDeviceMetricsOverride', {
  width: 1440, height: 1000, deviceScaleFactor: 2, mobile: false,
})
await send('Page.navigate', { url: URL_ })
await sleep(2500)

// React owns the textarea's value, so set it through the native setter and
// dispatch an input event -- assigning .value directly does not reach state.
await evaluate(`(() => {
  const setter = Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype, 'value').set
  const box = document.querySelector('textarea.complaint')
  setter.call(box, ${JSON.stringify(
    'hi, i tried to buy stuff this morning and it charged me postage even though the site says free postage over $50. my basket was definitely more than $50. can you sort it out, we have customers complaining'
  )})
  box.dispatchEvent(new Event('input', { bubbles: true }))
  return true
})()`)
await sleep(400)

// Pick the seeded shopcart checkout: the "Demo repo" segment, then the option.
await evaluate(`(() => {
  const seg = [...document.querySelectorAll('.seg')].find(b => /demo/i.test(b.textContent))
  if (seg) seg.click()
  return true
})()`)
await sleep(600)
await evaluate(`(() => {
  const pick = [...document.querySelectorAll('button, option, .repo-choice, li')]
    .find(el => /shopcart/i.test(el.textContent) && el.offsetParent !== null)
  if (pick) pick.click()
  const sel = document.querySelector('select')
  if (sel) {
    const opt = [...sel.options].find(o => /shopcart/i.test(o.textContent))
    if (opt) {
      const setter = Object.getOwnPropertyDescriptor(window.HTMLSelectElement.prototype,'value').set
      setter.call(sel, opt.value)
      sel.dispatchEvent(new Event('change', { bubbles: true }))
    }
  }
  return true
})()`)
await sleep(800)
await shot('00-form')

await evaluate(`(() => {
  const run = [...document.querySelectorAll('button')].find(b => b.textContent.trim() === 'Run' && !b.disabled)
  if (!run) throw new Error('Run button not enabled')
  run.click(); return true
})()`)
console.log('run started')

// The run streams into the "Run" tab; the Code tab is what is showing.
await sleep(1000)
await evaluate(`(() => {
  const tab = [...document.querySelectorAll('button, a, [role=tab]')]
    .find(el => el.textContent.trim() === 'Run' && !/panel|form/.test(el.closest('form') ? 'form' : ''))
  const tabs = [...document.querySelectorAll('button, a, [role=tab]')].filter(el => el.textContent.trim() === 'Run')
  const outside = tabs.find(el => !el.closest('form'))
  ;(outside || tab)?.click()
  return true
})()`)
await sleep(1500)

// Every card stays on screen for the whole run, so wait for the END and then
// scroll back to each of the three moments. Far more robust than racing them.
await until('client reply (run finished)', `document.querySelector('.reply-body') !== null`, 180000)
await sleep(2000)

const scrollShoot = async (name, findExpr) => {
  await evaluate(`(() => { const el = ${findExpr}; if (!el) throw new Error('not found: ${name}'); el.scrollIntoView({ block: 'center' }); return true })()`)
  await sleep(900)
  await shot(name)
}

// Moment 1 - the first repro attempt: the test went GREEN, so it did NOT
// reproduce. The 15 seconds the video must not cut.
await scrollShoot('01-first-repro-failed',
  `[...document.querySelectorAll('.eyebrow')].find(e => /Reproduce . attempt 1/.test(e.textContent))?.closest('.tl-item, section')`)

// Moment 2 - the accepted fix: repro test GREEN and the pre-existing suite GREEN.
await scrollShoot('02-two-green-badges',
  `[...document.querySelectorAll('.card-fix')].pop()`)

// Moment 3 - the loop closed with the person who complained.
await scrollShoot('03-client-reply', `document.querySelector('.reply')`)

console.log('done')
process.exit(0)
