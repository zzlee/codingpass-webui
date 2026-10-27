/**
 * Tiny data-loading hook shared by the Problem Bank, Classrooms and Submissions.
 *
 * Why not `useEffect(() => setLoading(true), [query])`? Because that is a
 * cascading render: the effect sets state synchronously, which re-renders, which
 * fetches. `useAsync` derives the loading flag during render instead - a request
 * is "in flight" exactly when the settled snapshot does not belong to the current
 * key - so a filter change paints one frame with a spinner and nothing else
 * re-renders twice.
 *
 * It also gives stale-while-revalidate for free: while a new request is running,
 * `data` is `null` but the previous snapshot can be kept by the caller if it
 * wants to avoid flicker.
 */
import { useCallback, useEffect, useRef, useState } from 'react'

import { ApiError } from './api'

/** Human readable message for any thrown value. */
export function describeError(error: unknown): string {
  if (error instanceof ApiError) return error.detail || error.message
  return error instanceof Error ? error.message : String(error)
}

export interface AsyncValue<T> {
  /** Settled payload for the current key, or `null` while loading/failed. */
  data: T | null
  /** Settled error for the current key. */
  error: string | null
  /** `true` while the request for the current key has not settled. */
  loading: boolean
  /** Re-run the current request (wired to the Refresh buttons). */
  reload: () => void
  /** Identifies the in-flight request; store it alongside appended pages. */
  token: string
}

/**
 * @param key   serialised identity of the request (filters, id, page size…)
 * @param work  the async call; read through a ref so a fresh closure is always used
 */
export function useAsync<T>(key: string, work: () => Promise<T>): AsyncValue<T> {
  const [tick, setTick] = useState(0)
  const [settled, setSettled] = useState<{ token: string; data: T | null; error: string | null }>({
    token: '',
    data: null,
    error: null,
  })

  const workRef = useRef(work)
  useEffect(() => {
    workRef.current = work
  })

  const token = `${key}#${tick}`

  useEffect(() => {
    let cancelled = false
    void (async () => {
      try {
        const data = await workRef.current()
        if (!cancelled) setSettled({ token, data, error: null })
      } catch (error) {
        if (!cancelled) setSettled({ token, data: null, error: describeError(error) })
      }
    })()
    return () => {
      cancelled = true
    }
    // `token` carries both the key and the manual-reload tick, and `workRef`
    // keeps the closure fresh, so nothing else belongs in the dependency list.
  }, [token])

  const reload = useCallback(() => setTick((previous) => previous + 1), [])
  const current = settled.token === token

  return {
    data: current ? settled.data : null,
    error: current ? settled.error : null,
    loading: !current,
    reload,
    token,
  }
}

export default useAsync
