/**
 * `Ctrl/Cmd + Enter` routing.
 *
 * One key chord, one owner at a time: each mounted view registers a handler and
 * the most recently mounted one wins. In the Playground that is "Run Code", in
 * the Problem Solver it is "Run Samples"; a view with no handler (Classrooms)
 * simply leaves the chord inert, so nothing is ever judged by accident.
 *
 * The listener itself lives in `<WorkspaceProvider />`, which installs it once
 * for the whole tree in the capture phase - before Monaco consumes the event.
 */
import { useEffect, useRef } from 'react'

type RunHandler = () => void

interface Entry {
  id: number
  run: RunHandler
}

/** Mounted views in mount order; the last entry owns the chord. */
const stack: Entry[] = []
let seed = 0

/** Fire the active handler, if any. Returns whether one ran. */
export function firePrimaryShortcut(): boolean {
  const entry = stack[stack.length - 1]
  if (!entry) return false
  entry.run()
  return true
}

/**
 * Own `Ctrl/Cmd + Enter` while this view is mounted.
 *
 * The handler is read through a ref, so callers may pass a fresh closure on every
 * render without re-registering (and without ever calling a stale one).
 */
export function useCtrlEnter(run: RunHandler): void {
  const latest = useRef(run)

  useEffect(() => {
    latest.current = run
  })

  useEffect(() => {
    const entry: Entry = { id: ++seed, run: () => latest.current() }
    stack.push(entry)
    return () => {
      const index = stack.indexOf(entry)
      if (index >= 0) stack.splice(index, 1)
    }
  }, [])
}
