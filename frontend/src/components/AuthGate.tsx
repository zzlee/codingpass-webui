/**
 * Placeholder shown on tabs that need an account (Problem Bank, Classrooms,
 * Submissions). The Playground deliberately keeps working without one because
 * `POST /api/run` is public - only graded features require a session.
 */
import { Lock, LogIn } from 'lucide-react'

import { buttonClass } from '../format'
import { QUICK_ACCOUNTS } from '../authAccounts'

export interface AuthGateProps {
  title: string
  hint: string
  onSignIn: () => void
}

export function AuthGate({ title, hint, onSignIn }: AuthGateProps) {
  return (
    <div className="flex min-h-0 flex-1 items-center justify-center overflow-y-auto p-6">
      <div className="w-full max-w-lg rounded-2xl border border-slate-800 bg-slate-900/40 p-6 text-center shadow-xl shadow-slate-950/60">
        <span className="mx-auto grid h-12 w-12 place-items-center rounded-xl bg-slate-800/80 text-sky-400">
          <Lock className="h-6 w-6" aria-hidden="true" />
        </span>
        <h2 className="mt-4 text-base font-semibold text-white">{title}</h2>
        <p className="mx-auto mt-1 max-w-sm text-xs leading-relaxed text-slate-400">{hint}</p>

        <button
          type="button"
          onClick={onSignIn}
          className={buttonClass('accent', 'mt-4 w-full sm:w-auto')}
        >
          <LogIn className="h-4 w-4" aria-hidden="true" />
          Sign in or register
        </button>

        <div className="mt-5 border-t border-slate-800 pt-4">
          <p className="text-[11px] font-semibold uppercase tracking-wider text-slate-500">
            Or try a seeded account
          </p>
          <ul className="mt-2 grid gap-1.5 text-left sm:grid-cols-2">
            {QUICK_ACCOUNTS.map((account) => (
              <li
                key={account.key}
                className="flex items-center gap-2 rounded-lg border border-slate-800 bg-slate-950/40 px-2.5 py-1.5"
              >
                <span aria-hidden="true">{account.emoji}</span>
                <span className="min-w-0 flex-1 leading-tight">
                  <span className="block truncate text-[11px] font-semibold text-slate-200">
                    {account.label}
                  </span>
                  <span className="block truncate font-mono text-[10px] text-slate-500">
                    {account.hint}
                  </span>
                </span>
              </li>
            ))}
          </ul>
        </div>
      </div>
    </div>
  )
}

export default AuthGate
