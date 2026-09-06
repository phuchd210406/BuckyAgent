import { createContext, useContext } from 'react'

/**
 * "Show me that file" — from anywhere in the timeline.
 *
 * A card deep inside the timeline (a hypothesis, the test an attempt wrote)
 * knows a path and nothing else; the browser panel is three components away and
 * two levels up. Passing a callback down through Timeline and TimelineCard
 * would thread a prop through components that have no other reason to know the
 * file browser exists.
 *
 * Defaults to a no-op, so a card rendered outside the app — in a test, or in
 * the server-render check — is not required to provide one.
 */
export const OpenFileContext = createContext(null)

export function useOpenFile() {
  return useContext(OpenFileContext)
}
