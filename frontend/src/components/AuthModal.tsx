/**
 * Login / register dialog.
 *
 * Two modes in one modal because that is what a classroom actually needs: the
 * teacher projects the screen, a student picks a demo account (or types their
 * own credentials) and is in within a click or two. Registration is public and
 * always produces a `student` account - the API enforces that, the form simply
 * explains it.
 */
import { useCallback, useState, type FormEvent } from 'react'
import { CircleAlert, LogIn, Rocket, ShieldCheck, UserPlus } from 'lucide-react'

import { Field, Modal, RoleBadge, Spinner } from './ui'
import { useAuth } from '../authContext'
import { INPUT_CLASS, buttonClass } from '../format'

type Mode = 'login' | 'register'

const USERNAME_PATTERN = /^[A-Za-z][A-Za-z0-9._-]{2,31}$/
const EMAIL_PATTERN = /^[^\s@]+@[^\s@]+\.[^\s@]+$/

export interface AuthModalProps {
  open: boolean
  onClose: () => void
}

export function AuthModal({ open, onClose }: AuthModalProps) {
  const { busy, error, login, register, quickAccounts, loginWithAccount, clearError } = useAuth()
  const [mode, setMode] = useState<Mode>('login')
  const [identifier, setIdentifier] = useState('')
  const [password, setPassword] = useState('')
  const [username, setUsername] = useState('')
  const [email, setEmail] = useState('')
  const [fullName, setFullName] = useState('')
  const [confirm, setConfirm] = useState('')
  const [formError, setFormError] = useState<string | null>(null)

  // Errors are cleared when the dialog closes rather than when it opens: the
  // effect form would paint one frame with a stale message on every reopen.
  const close = useCallback(() => {
    setFormError(null)
    clearError()
    onClose()
  }, [clearError, onClose])

  const shown = formError ?? error

  const submit = async (event: FormEvent) => {
    event.preventDefault()
    if (busy) return
    setFormError(null)

    if (mode === 'login') {
      if (!identifier.trim() || !password) {
        setFormError('Enter your username (or e-mail) and password.')
        return
      }
      if (await login(identifier, password)) close()
      return
    }

    if (!USERNAME_PATTERN.test(username.trim())) {
      setFormError(
        'Username must be 3-32 characters, start with a letter and use letters, digits, ".", "_" or "-".',
      )
      return
    }
    if (!EMAIL_PATTERN.test(email.trim())) {
      setFormError('Enter a valid e-mail address.')
      return
    }
    if (password.length < 6) {
      setFormError('Password must be at least 6 characters.')
      return
    }
    if (password !== confirm) {
      setFormError('The two passwords do not match.')
      return
    }
    const ok = await register({ username, email, password, full_name: fullName })
    if (ok) close()
  }

  return (
    <Modal
      open={open}
      onClose={close}
      title={mode === 'login' ? 'Sign in to CodingPass' : 'Create a student account'}
      subtitle={
        mode === 'login'
          ? 'Use your own credentials, or jump straight into a demo role.'
          : 'Public sign-ups are students. Teachers and admins are created by an admin.'
      }
      icon={<LogIn className="h-5 w-5" aria-hidden="true" />}
      footer={
        <>
          <button
            type="button"
            onClick={() => {
              setMode(mode === 'login' ? 'register' : 'login')
              setFormError(null)
              clearError()
            }}
            className="mr-auto inline-flex items-center gap-2 text-xs font-semibold text-sky-400 transition-colors hover:text-sky-300"
          >
            {mode === 'login' ? <UserPlus className="h-3.5 w-3.5" aria-hidden="true" /> : null}
            {mode === 'login' ? 'No account yet? Register' : 'I already have an account'}
          </button>
          <button type="button" onClick={close} className={buttonClass('ghost')}>
            Cancel
          </button>
          <button type="submit" form="codingpass-auth-form" disabled={busy} className={buttonClass('accent')}>
            {busy ? <Spinner /> : mode === 'login' ? <LogIn className="h-4 w-4" aria-hidden="true" /> : <UserPlus className="h-4 w-4" aria-hidden="true" />}
            <span>{busy ? 'Please wait…' : mode === 'login' ? 'Sign in' : 'Register'}</span>
          </button>
        </>
      }
    >
      <form id="codingpass-auth-form" onSubmit={(event) => void submit(event)} className="space-y-3">
        {mode === 'login' ? (
          <>
            <Field label="Username or e-mail" htmlFor="auth-identifier">
              <input
                id="auth-identifier"
                autoComplete="username"
                value={identifier}
                onChange={(event) => setIdentifier(event.target.value)}
                placeholder="student1"
                className={INPUT_CLASS}
              />
            </Field>
            <Field label="Password" htmlFor="auth-password">
              <input
                id="auth-password"
                type="password"
                autoComplete="current-password"
                value={password}
                onChange={(event) => setPassword(event.target.value)}
                placeholder="••••••••"
                className={INPUT_CLASS}
              />
            </Field>
          </>
        ) : (
          <>
            <div className="grid gap-3 sm:grid-cols-2">
              <Field label="Username" hint="3-32 chars" htmlFor="reg-username">
                <input
                  id="reg-username"
                  autoComplete="username"
                  value={username}
                  onChange={(event) => setUsername(event.target.value)}
                  placeholder="carol"
                  className={INPUT_CLASS}
                />
              </Field>
              <Field label="Full name" hint="optional" htmlFor="reg-name">
                <input
                  id="reg-name"
                  autoComplete="name"
                  value={fullName}
                  onChange={(event) => setFullName(event.target.value)}
                  placeholder="Carol Lin"
                  className={INPUT_CLASS}
                />
              </Field>
            </div>
            <Field label="E-mail" htmlFor="reg-email">
              <input
                id="reg-email"
                type="email"
                autoComplete="email"
                value={email}
                onChange={(event) => setEmail(event.target.value)}
                placeholder="carol@example.edu"
                className={INPUT_CLASS}
              />
            </Field>
            <div className="grid gap-3 sm:grid-cols-2">
              <Field label="Password" hint="min 6 chars" htmlFor="reg-password">
                <input
                  id="reg-password"
                  type="password"
                  autoComplete="new-password"
                  value={password}
                  onChange={(event) => setPassword(event.target.value)}
                  className={INPUT_CLASS}
                />
              </Field>
              <Field label="Confirm password" htmlFor="reg-confirm">
                <input
                  id="reg-confirm"
                  type="password"
                  autoComplete="new-password"
                  value={confirm}
                  onChange={(event) => setConfirm(event.target.value)}
                  className={INPUT_CLASS}
                />
              </Field>
            </div>
            <p className="flex items-start gap-2 text-[11px] text-slate-500">
              <ShieldCheck className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden="true" />
              Passwords are hashed with bcrypt server-side; only the first 72 UTF-8 bytes are used.
            </p>
          </>
        )}

        {shown ? (
          <p
            role="alert"
            className="flex items-start gap-2 rounded-lg border border-rose-500/40 bg-rose-500/10 px-3 py-2 text-xs text-rose-200"
          >
            <CircleAlert className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
            <span className="min-w-0 break-words">{shown}</span>
          </p>
        ) : null}
      </form>

      {mode === 'login' ? (
        <div className="mt-4 border-t border-slate-800 pt-3">
          <p className="mb-2 flex items-center gap-2 text-[11px] font-semibold uppercase tracking-wider text-slate-500">
            <Rocket className="h-3.5 w-3.5" aria-hidden="true" />
            One-click demo accounts
          </p>
          <div className="grid gap-2 sm:grid-cols-2">
            {quickAccounts.map((account) => (
              <button
                key={account.key}
                type="button"
                disabled={busy}
                onClick={() => {
                  void loginWithAccount(account).then((ok) => {
                    if (ok) close()
                  })
                }}
                className="flex items-center gap-2 rounded-lg border border-slate-700 bg-slate-900/70 px-2.5 py-2 text-left transition-colors hover:border-sky-500/60 hover:bg-slate-800 disabled:cursor-not-allowed disabled:opacity-60"
              >
                <span className="text-base leading-none" aria-hidden="true">
                  {account.emoji}
                </span>
                <span className="min-w-0 flex-1 leading-tight">
                  <span className="block truncate text-xs font-semibold text-slate-100">
                    {account.label}
                  </span>
                  <span className="block truncate font-mono text-[10px] text-slate-500">
                    {account.hint}
                  </span>
                </span>
                <RoleBadge role={account.role} />
              </button>
            ))}
          </div>
        </div>
      ) : null}
    </Modal>
  )
}

export default AuthModal
