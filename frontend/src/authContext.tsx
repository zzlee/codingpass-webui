/**
 * Authentication state for the whole application.
 *
 * Responsibilities:
 *  - hold the signed-in account (`user`), the bearer token and the role;
 *  - keep the token in `localStorage` and revalidate it against
 *    `GET /api/auth/me` on boot, so a stale token never renders a ghost session;
 *  - expose one-click demo logins (role switcher) for classroom demos;
 *  - expose the classic login / register form actions used by `AuthModal`.
 *
 * The provider deliberately owns *all* auth mutations so no panel can end up
 * with a half-updated session.
 */
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from 'react'

import {
  api,
  ApiError,
  readStoredToken,
  setUnauthorizedHandler,
  storeToken,
  storedTokenIsUsable,
} from './api'
import { QUICK_ACCOUNTS, type QuickAccount } from './authAccounts'
import type { Role, User } from './types'

/** Fields collected by the register form. */
export interface RegisterInput {
  username: string
  email: string
  password: string
  full_name: string
}

interface AuthContextValue {
  /** Signed-in account, or `null` for a guest. */
  user: User | null
  /** Raw bearer token (kept for display/debugging; the API reads storage itself). */
  token: string | null
  /** Convenience shortcut for role gated UI. `null` while signed out. */
  role: Role | null
  /** `true` once the boot revalidation finished (avoids flash-of-signed-out). */
  ready: boolean
  /** `true` while a login/register round trip is in flight. */
  busy: boolean
  /** Last auth error, already humanised. Cleared by the next attempt. */
  error: string | null
  quickAccounts: readonly QuickAccount[]
  /** Sign in with username or e-mail. Resolves `true` on success. */
  login: (identifier: string, password: string) => Promise<boolean>
  /** One-click demo sign-in from the role switcher. */
  loginWithAccount: (account: QuickAccount) => Promise<boolean>
  /** Public sign-up (always creates a student). */
  register: (input: RegisterInput) => Promise<boolean>
  /** Drop the session and clear the stored token. */
  logout: () => void
  /** Re-read `/api/auth/me`, e.g. after editing the profile. */
  refresh: () => Promise<void>
  /** Clear the error line (called when a modal reopens). */
  clearError: () => void
}

const AuthContext = createContext<AuthContextValue | null>(null)

/** Error text for auth failures; falls back to a generic line. */
function authMessage(error: unknown): string {
  if (error instanceof ApiError) return error.detail || error.message
  return error instanceof Error ? error.message : String(error)
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null)
  const [token, setToken] = useState<string | null>(null)
  // Derived at first render: with no stored token there is nothing to validate,
  // so the UI is "ready" immediately and never flashes a signed-out screen.
  const [ready, setReady] = useState(() => {
    const stored = readStoredToken()
    return !stored || !storedTokenIsUsable()
  })
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  // Guards against a slower second login overwriting a newer one.
  const attemptRef = useRef(0)

  const applySession = useCallback((nextToken: string, nextUser: User, expiresIn: number) => {
    storeToken(nextToken, expiresIn)
    setToken(nextToken)
    setUser(nextUser)
    setError(null)
  }, [])

  const logout = useCallback(() => {
    storeToken(null)
    setToken(null)
    setUser(null)
    setError(null)
  }, [])

  /** Boot: adopt the stored token, but only if the API still accepts it. */
  useEffect(() => {
    let cancelled = false
    const stored = readStoredToken()

    if (!stored || !storedTokenIsUsable()) {
      if (stored) storeToken(null)
      return () => {
        cancelled = true
      }
    }

    void (async () => {
      try {
        const me = await api.me()
        if (!cancelled) {
          setUser(me)
          setToken(stored)
        }
      } catch (reason) {
        // Only a rejected token kills the session; a dead backend keeps it so a
        // judge restart does not sign the whole class out.
        const rejected = reason instanceof ApiError && (reason.status === 401 || reason.status === 403)
        if (!cancelled && rejected) {
          storeToken(null)
          setToken(null)
          setUser(null)
        }
      } finally {
        if (!cancelled) setReady(true)
      }
    })()

    return () => {
      cancelled = true
    }
  }, [])

  /** Any authenticated call that comes back 401 ends the session everywhere. */
  useEffect(() => {
    setUnauthorizedHandler(() => {
      storeToken(null)
      setToken(null)
      setUser(null)
      setError('Your session expired. Please sign in again.')
    })
    return () => setUnauthorizedHandler(null)
  }, [])

  const login = useCallback(
    async (identifier: string, password: string): Promise<boolean> => {
      const attempt = ++attemptRef.current
      setBusy(true)
      setError(null)
      try {
        const reply = await api.login(identifier.trim(), password)
        if (attempt !== attemptRef.current) return false
        applySession(reply.access_token, reply.user, reply.expires_in)
        return true
      } catch (reason) {
        if (attempt === attemptRef.current) setError(authMessage(reason))
        return false
      } finally {
        if (attempt === attemptRef.current) setBusy(false)
      }
    },
    [applySession],
  )

  const loginWithAccount = useCallback(
    async (account: QuickAccount): Promise<boolean> => login(account.username, account.password),
    [login],
  )

  const register = useCallback(
    async (input: RegisterInput): Promise<boolean> => {
      const attempt = ++attemptRef.current
      setBusy(true)
      setError(null)
      try {
        // Registration returns the account only; sign straight in with the same
        // credentials so the UI never sits in a "registered but signed out" state.
        await api.register({
          username: input.username.trim(),
          email: input.email.trim(),
          password: input.password,
          full_name: input.full_name.trim(),
        })
        const reply = await api.login(input.username.trim(), input.password)
        if (attempt !== attemptRef.current) return false
        applySession(reply.access_token, reply.user, reply.expires_in)
        return true
      } catch (reason) {
        if (attempt === attemptRef.current) setError(authMessage(reason))
        return false
      } finally {
        if (attempt === attemptRef.current) setBusy(false)
      }
    },
    [applySession],
  )

  const refresh = useCallback(async (): Promise<void> => {
    if (!readStoredToken()) return
    try {
      setUser(await api.me())
    } catch (reason) {
      if (reason instanceof ApiError && (reason.status === 401 || reason.status === 403)) logout()
    }
  }, [logout])

  const clearError = useCallback(() => setError(null), [])

  const value = useMemo<AuthContextValue>(
    () => ({
      user,
      token,
      role: user?.role ?? null,
      ready,
      busy,
      error,
      quickAccounts: QUICK_ACCOUNTS,
      login,
      loginWithAccount,
      register,
      logout,
      refresh,
      clearError,
    }),
    [user, token, ready, busy, error, login, loginWithAccount, register, logout, refresh, clearError],
  )

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

/** Access the auth session. Throws outside `<AuthProvider />`. */
export function useAuth(): AuthContextValue {
  const context = useContext(AuthContext)
  if (!context) throw new Error('useAuth must be used inside <AuthProvider>')
  return context
}

export default AuthProvider
