/** Composing the client reply. Pure, so it can be checked without a browser. */

const GREETING = /^(hi|hello|hey|dear)\b/i

/**
 * The reply exactly as it is shown AND exactly as it is copied.
 *
 * One function for both, deliberately: a Copy button that puts something
 * different on the clipboard than what is on the screen is worse than no
 * button. The greeting is envelope, not content -- it is added only when the
 * writer did not already write one, and never rewords what they did write.
 */
export function composeReply(text, reporterName) {
  const body = (text || '').trim()
  if (!body) return ''
  if (GREETING.test(body)) return body
  const firstName = (reporterName || '').trim().split(/\s+/)[0]
  return `${firstName ? `Hi ${firstName},` : 'Hello,'}\n\n${body}`
}

export function paragraphsOf(reply) {
  return reply.split(/\n\s*\n/).filter(Boolean)
}
