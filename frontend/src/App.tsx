/**
 * CodingPass application shell.
 *
 * Composition:
 *   `<AuthProvider />`      - session, token, 1-click role switching
 *   `<WorkspaceProvider />` - Playground buffers, judge health, unsaved solutions
 *                             and the single Ctrl/Cmd + Enter listener
 *   `<Shell />`             - the four tabs and the sign-in modal
 *
 * Tab state (and the opened problem) lives here rather than inside the views, so
 * switching to Classrooms and back restores exactly where the student was, while
 * the views themselves unmount to keep one Monaco instance in memory instead of
 * four hidden ones.
 */
import { useCallback, useState } from 'react'

import AuthProvider, { useAuth } from './authContext'
import WorkspaceProvider from './workspaceContext'
import { LocalAgentProvider, useLocalAgent } from './localAgentContext'
import Header from './components/Header'
import Playground from './components/Playground'
import ProblemBank from './components/ProblemBank'
import ProblemSolver from './components/ProblemSolver'
import Classrooms from './components/Classrooms'
import Submissions from './components/Submissions'
import AgentModal from './components/AgentModal'
import AuthModal from './components/AuthModal'
import AuthGate from './components/AuthGate'
import { LoadingRow } from './components/ui'
import type { AppTab } from './types'

const AUTH_GATES: Record<Exclude<AppTab, 'playground'>, { title: string; hint: string }> = {
  problems: {
    title: 'Sign in to open the Problem Bank',
    hint: 'Graded exercises are attached to classrooms, so the judge needs to know who is submitting.',
  },
  classrooms: {
    title: 'Sign in to see your classrooms',
    hint: 'Teachers manage classes and invite codes here; students join with an invite code.',
  },
  submissions: {
    title: 'Sign in to see submissions',
    hint: 'Students see their own attempts; teachers see the attempts of the classes they teach.',
  },
}

function Shell() {
  const { user, ready } = useAuth()
  const { isModalOpen, closeModal, modalTab } = useLocalAgent()
  const [tab, setTab] = useState<AppTab>('playground')
  /** Open problem plus the account that opened it. */
  const [opened, setOpened] = useState<{ id: number; ownerId: number | null } | null>(null)
  const [authOpen, setAuthOpen] = useState(false)

  const openProblem = useCallback(
    (id: number) => {
      setTab('problems')
      setOpened({ id, ownerId: user?.id ?? null })
    },
    [user?.id],
  )

  const changeTab = useCallback((next: AppTab) => {
    setTab(next)
    if (next !== 'problems') setOpened(null)
  }, [])

  // A different account means different permissions, so an open problem is only
  // kept while the same identity is still signed in - derived, not reset by an
  // effect, which would cost an extra render on every login.
  const problemId = opened && opened.ownerId === (user?.id ?? null) ? opened.id : null

  const gate = tab === 'playground' ? null : AUTH_GATES[tab]
  const gated = gate !== null && !user

  return (
    <div className="flex h-screen min-h-0 flex-col bg-slate-950 font-sans text-slate-200">
      <Header activeTab={tab} onTabChange={changeTab} onSignIn={() => setAuthOpen(true)} />

      <main className="flex min-h-0 flex-1 flex-col">
        {tab === 'playground' ? (
          <Playground />
        ) : !ready && !user ? (
          <LoadingRow label="Restoring your session…" />
        ) : gated && gate ? (
          <AuthGate title={gate.title} hint={gate.hint} onSignIn={() => setAuthOpen(true)} />
        ) : tab === 'problems' ? (
          problemId === null ? (
            <ProblemBank onOpen={openProblem} />
          ) : (
            <ProblemSolver
              key={problemId}
              problemId={problemId}
              onBack={() => setOpened(null)}
            />
          )
        ) : tab === 'classrooms' ? (
          <Classrooms onOpenProblem={openProblem} />
        ) : (
          <Submissions />
        )}
      </main>

      <AuthModal open={authOpen} onClose={() => setAuthOpen(false)} />
      <AgentModal open={isModalOpen} onClose={closeModal} initialTab={modalTab} />
    </div>
  )
}

export default function App() {
  return (
    <AuthProvider>
      <WorkspaceProvider>
        <LocalAgentProvider>
          <Shell />
        </LocalAgentProvider>
      </WorkspaceProvider>
    </AuthProvider>
  )
}
