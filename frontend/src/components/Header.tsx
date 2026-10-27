/**
 * Application header: branding, the four primary tabs, the judge health pill and
 * the account area (1-click demo role switcher + user menu).
 *
 * The role switcher exists for lessons and demos: switching between Alice, Bob,
 * Prof. Chen and the admin is one click because the seeded passwords are known,
 * which makes "student view vs teacher view" a two-second exercise instead of a
 * four-minute typing one.
 */
import { useEffect, useRef, useState } from 'react'
import {
  ChevronDown,
  Check,
  CircleAlert,
  CircleCheck,
  CircleDashed,
  Download,
  Laptop,
  LogIn,
  LogOut,
  RefreshCw,
  SquareTerminal,
  WifiOff,
  Zap,
  type LucideIcon,
} from 'lucide-react'

import { RoleBadge } from './ui'
import AgentModal from './AgentModal'
import { APP_TABS } from '../tabs'
import { useAuth } from '../authContext'
import type { QuickAccount } from '../authAccounts'
import { useWorkspace } from '../workspaceContext'
import { useLocalAgent } from '../localAgentContext'
import { displayName, initials } from '../format'
import type { AppTab, BackendHealth } from '../types'

/* -------------------------------------------------------------------------- */

interface HealthPillStyle {
  className: string
  dotClassName: string
  Icon: LucideIcon
  label: string
}

const HEALTH_STYLES: Record<BackendHealth['status'], HealthPillStyle> = {
  checking: {
    className: 'border-slate-700 bg-slate-800/60 text-slate-300',
    dotClassName: 'bg-slate-400',
    Icon: CircleDashed,
    label: 'Checking',
  },
  online: {
    className: 'border-emerald-500/40 bg-emerald-500/10 text-emerald-300',
    dotClassName: 'bg-emerald-400 shadow-[0_0_8px_2px_rgba(52,211,153,0.55)]',
    Icon: CircleCheck,
    label: 'Judge online',
  },
  degraded: {
    className: 'border-amber-500/40 bg-amber-500/10 text-amber-300',
    dotClassName: 'bg-amber-400 shadow-[0_0_8px_2px_rgba(251,191,36,0.5)]',
    Icon: CircleAlert,
    label: 'Judge degraded',
  },
  offline: {
    className: 'border-rose-500/40 bg-rose-500/10 text-rose-300',
    dotClassName: 'bg-rose-500',
    Icon: WifiOff,
    label: 'Judge offline',
  },
}

function HealthPill({ health, onHealthCheck }: { health: BackendHealth; onHealthCheck: () => void }) {
  const style = HEALTH_STYLES[health.status]
  const Icon = style.Icon
  return (
    <button
      type="button"
      onClick={onHealthCheck}
      title={`${health.detail}\nClick to re-check /api/health`}
      aria-label={`Backend status: ${style.label}. Re-check now.`}
      className={`group inline-flex items-center gap-2 rounded-full border px-2.5 py-1.5 text-xs font-medium transition-colors hover:brightness-125 focus:outline-none focus-visible:ring-2 focus-visible:ring-sky-400 ${style.className}`}
    >
      <span className={`h-2 w-2 shrink-0 rounded-full ${style.dotClassName}`} aria-hidden="true" />
      <Icon className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
      <span className="hidden whitespace-nowrap xl:inline">{style.label}</span>
      <RefreshCw
        className={`h-3 w-3 shrink-0 opacity-0 transition-opacity group-hover:opacity-70 ${
          health.status === 'checking' ? 'animate-spin opacity-100' : ''
        }`}
        aria-hidden="true"
      />
    </button>
  )
}

function LocalAgentPill({ onOpenModal }: { onOpenModal: () => void }) {
  const { status, compilerInfo } = useLocalAgent()

  if (status === 'connected') {
    return (
      <button
        type="button"
        onClick={onOpenModal}
        title={`本地代理加速運作中\n編譯器: ${compilerInfo || 'g++ / clang++'}\n點擊查看設定與狀態`}
        className="group inline-flex items-center gap-1.5 rounded-full border border-sky-500/40 bg-sky-500/10 px-2.5 py-1.5 text-xs font-medium text-sky-300 transition-colors hover:brightness-125 focus:outline-none focus-visible:ring-2 focus-visible:ring-sky-400"
      >
        <span className="h-2 w-2 shrink-0 rounded-full bg-sky-400 shadow-[0_0_8px_2px_rgba(56,189,248,0.55)]" aria-hidden="true" />
        <Laptop className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
        <span className="hidden whitespace-nowrap xl:inline">本地加速已連線</span>
        <span className="xl:hidden">本地加速</span>
      </button>
    )
  }

  if (status === 'connecting') {
    return (
      <button
        type="button"
        onClick={onOpenModal}
        title="正在嘗試連線本地代理 (ws://127.0.0.1:8765)... 點擊查看設定"
        className="inline-flex items-center gap-1.5 rounded-full border border-slate-700 bg-slate-800/60 px-2.5 py-1.5 text-xs font-medium text-slate-400 hover:text-slate-300"
      >
        <span className="h-2 w-2 shrink-0 animate-pulse rounded-full bg-slate-400" aria-hidden="true" />
        <Laptop className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
        <span className="hidden whitespace-nowrap xl:inline">本地連線中…</span>
      </button>
    )
  }

  return (
    <button
      type="button"
      onClick={onOpenModal}
      title="未偵測到本地代理。點擊下載安裝包，讓學生電腦本地編譯分流伺服器負載！"
      className="group inline-flex items-center gap-1.5 rounded-full border border-slate-800 bg-slate-900/80 px-2.5 py-1.5 text-xs font-medium text-slate-400 transition-colors hover:border-slate-700 hover:bg-slate-800 hover:text-slate-200"
    >
      <Download className="h-3.5 w-3.5 shrink-0 text-slate-500 group-hover:text-sky-300" aria-hidden="true" />
      <span className="hidden whitespace-nowrap lg:inline">本地加速包</span>
      <span className="lg:hidden">加速包</span>
    </button>
  )
}

/* -------------------------------------------------------------------------- */

/** Close a popover on outside click or `Esc`. */
function useDismissable(onDismiss: () => void) {
  const ref = useRef<HTMLDivElement | null>(null)

  useEffect(() => {
    const onPointerDown = (event: PointerEvent) => {
      if (ref.current && !ref.current.contains(event.target as Node)) onDismiss()
    }
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onDismiss()
    }
    window.addEventListener('pointerdown', onPointerDown)
    window.addEventListener('keydown', onKeyDown)
    return () => {
      window.removeEventListener('pointerdown', onPointerDown)
      window.removeEventListener('keydown', onKeyDown)
    }
  }, [onDismiss])

  return ref
}

/**
 * Account area: the demo role switcher plus the signed-in identity.
 *
 * The switcher lists the seeded accounts; the active one is ticked, so switching
 * identity is a single click at any time.
 */
function AccountArea({ onSignIn }: { onSignIn: () => void }) {
  const { user, busy, loginWithAccount, logout, quickAccounts } = useAuth()
  const [open, setOpen] = useState(false)
  const [pendingKey, setPendingKey] = useState<string | null>(null)
  const popoverRef = useDismissable(() => setOpen(false))

  const switchTo = async (account: QuickAccount) => {
    if (busy || account.key === user?.username) {
      setOpen(false)
      return
    }
    setPendingKey(account.key)
    try {
      await loginWithAccount(account)
    } finally {
      setPendingKey(null)
      setOpen(false)
    }
  }

  return (
    <div className="relative" ref={popoverRef}>
      <button
        type="button"
        onClick={() => setOpen((previous) => !previous)}
        aria-expanded={open}
        aria-haspopup="menu"
        className="inline-flex items-center gap-2 rounded-lg border border-slate-700 bg-slate-900/80 py-1.5 pl-2 pr-2 text-left transition-colors hover:border-slate-600 focus:outline-none focus-visible:ring-2 focus-visible:ring-sky-400"
      >
        <span
          className="grid h-7 w-7 shrink-0 place-items-center rounded-md bg-gradient-to-br from-sky-500/80 to-indigo-500/80 font-mono text-[11px] font-bold text-slate-950"
          aria-hidden="true"
        >
          {initials(user)}
        </span>
        <span className="hidden min-w-0 leading-tight sm:block">
          <span className="block max-w-[9rem] truncate text-xs font-semibold text-slate-100">
            {displayName(user)}
          </span>
          <span className="block text-[10px] uppercase tracking-wider text-slate-500">
            {user?.role ?? 'guest'}
          </span>
        </span>
        <ChevronDown className="h-4 w-4 text-slate-500" aria-hidden="true" />
      </button>

      {open ? (
        <div
          role="menu"
          className="absolute right-0 z-40 mt-2 w-72 overflow-hidden rounded-xl border border-slate-700 bg-slate-900/95 shadow-2xl shadow-slate-950/80 backdrop-blur"
        >
          <div className="border-b border-slate-800 px-3 py-2">
            <p className="text-[11px] font-semibold uppercase tracking-wider text-slate-500">
              Quick login · role switcher
            </p>
            <p className="mt-0.5 text-[11px] text-slate-500">
              Seeded demo accounts - one click switches the whole UI.
            </p>
          </div>

          <ul className="max-h-72 overflow-y-auto py-1">
            {quickAccounts.map((account) => {
              const active = user?.username === account.username
              const pending = pendingKey === account.key
              return (
                <li key={account.key}>
                  <button
                    type="button"
                    role="menuitem"
                    onClick={() => void switchTo(account)}
                    disabled={busy}
                    className={`flex w-full items-center gap-2.5 px-3 py-2 text-left transition-colors hover:bg-slate-800 disabled:cursor-not-allowed disabled:opacity-60 ${
                      active ? 'bg-slate-800/60' : ''
                    }`}
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
                    {pending ? (
                      <RefreshCw className="h-3.5 w-3.5 animate-spin text-sky-400" aria-hidden="true" />
                    ) : active ? (
                      <Check className="h-3.5 w-3.5 text-emerald-400" aria-hidden="true" />
                    ) : null}
                  </button>
                </li>
              )
            })}
          </ul>

          <div className="flex items-center gap-2 border-t border-slate-800 px-3 py-2">
            {user ? (
              <button
                type="button"
                onClick={() => {
                  logout()
                  setOpen(false)
                }}
                className="inline-flex flex-1 items-center justify-center gap-2 rounded-lg border border-slate-700 bg-slate-900 px-2 py-1.5 text-xs font-semibold text-slate-300 transition-colors hover:border-rose-500/50 hover:text-rose-200"
              >
                <LogOut className="h-3.5 w-3.5" aria-hidden="true" />
                Sign out
              </button>
            ) : (
              <button
                type="button"
                onClick={() => {
                  setOpen(false)
                  onSignIn()
                }}
                className="inline-flex flex-1 items-center justify-center gap-2 rounded-lg bg-sky-500 px-2 py-1.5 text-xs font-semibold text-slate-950 transition-colors hover:bg-sky-400"
              >
                <LogIn className="h-3.5 w-3.5" aria-hidden="true" />
                Sign in / register
              </button>
            )}
          </div>
        </div>
      ) : null}
    </div>
  )
}

/* -------------------------------------------------------------------------- */

export interface HeaderProps {
  activeTab: AppTab
  onTabChange: (tab: AppTab) => void
  /** Open the sign-in / register modal. */
  onSignIn: () => void
}

export function Header({ activeTab, onTabChange, onSignIn }: HeaderProps) {
  const { health, checkHealth } = useWorkspace()
  const { user, ready } = useAuth()
  const [agentModalOpen, setAgentModalOpen] = useState(false)

  return (
    <>
      <header className="sticky top-0 z-30 border-b border-slate-800/80 bg-slate-950/85 backdrop-blur supports-[backdrop-filter]:bg-slate-950/70">
        <div className="flex flex-wrap items-center gap-x-4 gap-y-2 px-3 py-2.5 sm:px-4">
          {/* Branding ------------------------------------------------------ */}
          <div className="flex items-center gap-2.5">
            <span className="grid h-9 w-9 place-items-center rounded-xl bg-gradient-to-br from-sky-500 via-indigo-500 to-fuchsia-500 shadow-lg shadow-indigo-950/60">
              <SquareTerminal className="h-5 w-5 text-white" strokeWidth={2.2} aria-hidden="true" />
            </span>
            <div className="leading-tight">
              <div className="flex items-baseline gap-2">
                <span className="text-base font-semibold tracking-tight text-white">CodingPass</span>
                <span className="hidden rounded bg-slate-800 px-1.5 py-0.5 font-mono text-[10px] uppercase tracking-wider text-slate-400 lg:inline">
                  C/C++ judge
                </span>
              </div>
              <p className="hidden text-[11px] text-slate-400 sm:block">
                Sandbox runner · problem bank · classrooms
              </p>
            </div>
          </div>

          {/* Tabs ---------------------------------------------------------- */}
          <nav aria-label="Sections" className="-mx-1 flex min-w-0 flex-1 items-center gap-1 overflow-x-auto px-1">
            {APP_TABS.map((tab) => {
              const active = tab.id === activeTab
              const Icon = tab.Icon
              return (
                <button
                  key={tab.id}
                  type="button"
                  onClick={() => onTabChange(tab.id)}
                  aria-current={active ? 'page' : undefined}
                  title={tab.hint}
                  className={`inline-flex shrink-0 items-center gap-1.5 rounded-lg px-2.5 py-1.5 text-xs font-semibold transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-sky-400 ${
                    active
                      ? 'bg-sky-500/20 text-sky-200 ring-1 ring-inset ring-sky-400/50'
                      : 'text-slate-400 hover:bg-slate-800/80 hover:text-slate-200'
                  }`}
                >
                  <span aria-hidden="true">{tab.emoji}</span>
                  <Icon className="h-3.5 w-3.5" aria-hidden="true" />
                  <span className="whitespace-nowrap">{tab.label}</span>
                </button>
              )
            })}
          </nav>

          {/* Right hand side ----------------------------------------------- */}
          <div className="flex items-center gap-2">
            {!ready && !user ? (
              <span className="hidden rounded-md border border-slate-700/70 bg-slate-900/70 px-2 py-1 font-mono text-[10px] text-slate-500 md:inline">
                restoring session…
              </span>
            ) : null}
            {!user ? (
              <button
                type="button"
                onClick={onSignIn}
                title="Sign in with an account, or use a demo account from the menu"
                className="inline-flex items-center gap-2 rounded-lg bg-sky-500 px-3 py-1.5 text-xs font-semibold text-slate-950 transition-colors hover:bg-sky-400"
              >
                <Zap className="h-3.5 w-3.5" aria-hidden="true" />
                <span>Sign in</span>
              </button>
            ) : null}
            <LocalAgentPill onOpenModal={() => setAgentModalOpen(true)} />
            <HealthPill health={health} onHealthCheck={() => void checkHealth()} />
            <AccountArea onSignIn={onSignIn} />
          </div>
        </div>
      </header>
      <AgentModal open={agentModalOpen} onClose={() => setAgentModalOpen(false)} />
    </>
  )
}

export default Header
