import React from 'react'

/**
 * A file, with line numbers, lightly coloured.
 *
 * The colouring is ~60 lines of tokeniser rather than a syntax-highlighting
 * dependency, for two reasons. The bundle is served off a laptop behind a
 * tunnel during the demo, and the panel only ever shows Python, Markdown and
 * config — 90% of the legibility for none of the weight.
 *
 * It emits spans, never HTML: file contents come off somebody else's
 * repository, and the one thing this component must never do is let a file
 * decide what the page renders.
 */

const KEYWORDS = new Set(
  ('False None True and as assert async await break class continue def del elif else except ' +
   'finally for from global if import in is lambda nonlocal not or pass raise return try ' +
   'while with yield match case self cls').split(' '),
)

const BUILTINS = new Set(
  ('abs all any bool dict enumerate float format int isinstance len list map max min print ' +
   'range round set sorted str sum tuple type zip Exception ValueError TypeError KeyError ' +
   'RuntimeError').split(' '),
)

const IDENT = /[A-Za-z_][A-Za-z0-9_]*/y
const NUMBER = /\d[\d_]*(\.\d+)?/y

/** Split one line into {text, kind} tokens, given the triple-quote state. */
function tokenizeLine(line, state) {
  const out = []
  let i = 0
  const push = (text, kind) => text && out.push({ text, kind })

  // Still inside a docstring that opened on an earlier line.
  if (state.triple) {
    const close = line.indexOf(state.triple)
    if (close === -1) {
      push(line, 'str')
      return out
    }
    push(line.slice(0, close + 3), 'str')
    state.triple = null
    i = close + 3
  }

  let plain = ''
  const flush = () => {
    push(plain, 'plain')
    plain = ''
  }

  while (i < line.length) {
    const rest = line.slice(i)

    if (line[i] === '#') {
      flush()
      push(rest, 'comment')
      return out
    }

    const triple = rest.startsWith('"""') ? '"""' : rest.startsWith("'''") ? "'''" : null
    if (triple) {
      flush()
      const close = line.indexOf(triple, i + 3)
      if (close === -1) {
        push(rest, 'str')
        state.triple = triple
        return out
      }
      push(line.slice(i, close + 3), 'str')
      i = close + 3
      continue
    }

    if (line[i] === '"' || line[i] === "'") {
      flush()
      const quote = line[i]
      let j = i + 1
      while (j < line.length && line[j] !== quote) j += line[j] === '\\' ? 2 : 1
      push(line.slice(i, Math.min(j + 1, line.length)), 'str')
      i = j + 1
      continue
    }

    IDENT.lastIndex = i
    const word = IDENT.exec(line)
    if (word) {
      flush()
      const kind = KEYWORDS.has(word[0]) ? 'kw' : BUILTINS.has(word[0]) ? 'builtin' : 'plain'
      // `def name` / `class Name`: the name itself is the thing you scan for.
      const previous = out.filter((t) => t.kind !== 'plain' || t.text.trim()).pop()
      const named = previous && previous.kind === 'kw' && /^(def|class)$/.test(previous.text)
      push(word[0], named ? 'defname' : kind)
      i = IDENT.lastIndex
      continue
    }

    NUMBER.lastIndex = i
    const number = NUMBER.exec(line)
    if (number) {
      flush()
      push(number[0], 'num')
      i = NUMBER.lastIndex
      continue
    }

    plain += line[i]
    i += 1
  }
  flush()
  return out
}

export default function CodeView({ text, language = 'python' }) {
  const lines = (text || '').replace(/\n$/, '').split('\n')
  const state = { triple: null }
  const highlight = language === 'python'

  return (
    <div className="codeview">
      <ol className="codelines">
        {lines.map((line, index) => {
          const tokens = highlight ? tokenizeLine(line, state) : [{ text: line, kind: 'plain' }]
          return (
            <li key={index} className="codeline">
              <span className="lineno" aria-hidden="true">{index + 1}</span>
              <code className="linetext">
                {tokens.length === 0
                  ? ' '
                  : tokens.map((token, at) => (
                      <span key={at} className={`tok tok-${token.kind}`}>{token.text}</span>
                    ))}
              </code>
            </li>
          )
        })}
      </ol>
    </div>
  )
}
