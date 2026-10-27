/**
 * Tab 2 - the Problem Bank list.
 *
 * Students see the problems published to a class they joined; teachers see their
 * own plus everything in the classes they teach, and can filter to their private
 * bank. Each card carries the difficulty badge, the author, the sample/hidden
 * case counts and the per-case limits, which is everything needed to pick an
 * exercise before opening it.
 *
 * Paging is server side (`limit`/`offset`); difficulty, class and ownership are
 * API filters too, so the only client-side filter left is the free-text search
 * over the rows already on screen.
 */
import { useMemo, useState } from 'react'
import {
  BookOpen,
  Clock,
  Gauge,
  Layers,
  Lock,
  Plus,
  RefreshCw,
  School,
  Search,
  Sparkles,
} from 'lucide-react'

import { DifficultyBadge, EmptyState, ErrorNotice, LoadingRow, MetaChip, Segmented, Spinner } from './ui'
import ProblemCreateModal from './ProblemCreateModal'
import SectionHeading from './SectionHeading'
import { useAuth } from '../authContext'
import { api, type ProblemQuery } from '../api'
import { describeError, useAsync } from '../useAsync'
import { buttonClass, formatRelative, formatSeconds, groupThousands } from '../format'
import type { Difficulty, ProblemSummary } from '../types'

const PAGE_SIZE = 20

type DifficultyFilter = Difficulty | 'all'
/** `'all'` = every visible problem, `'private'` = the teacher's own bank. */
type ClassFilter = string

const DIFFICULTY_OPTIONS: Array<{ value: DifficultyFilter; label: string }> = [
  { value: 'all', label: 'All' },
  { value: 'easy', label: 'Easy' },
  { value: 'medium', label: 'Medium' },
  { value: 'hard', label: 'Hard' },
]

export interface ProblemBankProps {
  onOpen: (problemId: number) => void
}

export function ProblemBank({ onOpen }: ProblemBankProps) {
  const { user } = useAuth()
  const staff = user?.role === 'teacher' || user?.role === 'admin'

  const [difficulty, setDifficulty] = useState<DifficultyFilter>('all')
  const [classroomFilter, setClassroomFilter] = useState<ClassFilter>('all')
  const [mineOnly, setMineOnly] = useState(false)
  const [search, setSearch] = useState('')
  const [createOpen, setCreateOpen] = useState(false)
  const [extra, setExtra] = useState<{ token: string; rows: ProblemSummary[]; total: number } | null>(
    null,
  )
  const [loadingMore, setLoadingMore] = useState(false)
  const [moreError, setMoreError] = useState<string | null>(null)

  /** The API-side filter set; its serialisation keys the request. */
  const filters = useMemo<ProblemQuery>(
    () => ({
      difficulty: difficulty === 'all' ? '' : difficulty,
      classroom_id: /^[1-9]\d*$/.test(classroomFilter) ? Number(classroomFilter) : undefined,
      // The API has no "classroom_id IS NULL" filter, so the private bank is
      // requested as `mine=true` and narrowed locally.
      mine: staff && (mineOnly || classroomFilter === 'private') ? true : undefined,
    }),
    [difficulty, classroomFilter, mineOnly, staff],
  )
  const filterKey = JSON.stringify(filters)

  const list = useAsync(`problems:${user?.id}:${filterKey}`, () =>
    api.listProblems({ ...filters, limit: PAGE_SIZE, offset: 0 }),
  )
  const classroomList = useAsync(`classrooms:${user?.id}`, () => api.listClassrooms())
  const classrooms = classroomList.data ?? []

  const appended = extra && extra.token === list.token ? extra.rows : []
  const problems = list.data?.problems ?? []
  const rows = [...problems, ...appended]
  const total = extra && extra.token === list.token ? extra.total : (list.data?.total ?? 0)
  const error = list.error ?? moreError

  /** Title search (and the private-bank filter) stays local: paging is server side. */
  const needle = search.trim().toLowerCase()
  const visible = rows
    .filter((problem) => classroomFilter !== 'private' || problem.classroom_id === null)
    .filter(
      (problem) =>
        needle === '' ||
        `${problem.title} ${problem.teacher_name}`.toLowerCase().includes(needle),
    )

  const loadMore = async () => {
    if (loadingMore || rows.length >= total) return
    setLoadingMore(true)
    setMoreError(null)
    try {
      const reply = await api.listProblems({ ...filters, limit: PAGE_SIZE, offset: rows.length })
      setExtra((previous) =>
        previous && previous.token === list.token
          ? { token: list.token, rows: [...previous.rows, ...reply.problems], total: reply.total }
          : { token: list.token, rows: reply.problems, total: reply.total },
      )
    } catch (reason) {
      setMoreError(describeError(reason))
    } finally {
      setLoadingMore(false)
    }
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col overflow-hidden">
      <SectionHeading
        title="Problem Bank"
        hint={
          staff
            ? 'Open a problem to solve it, or publish a new exercise to one of your classes.'
            : 'Exercises published to your classes. Samples are shown; hidden cases only grade.'
        }
        icon={<BookOpen className="h-4 w-4" aria-hidden="true" />}
        actions={
          staff ? (
            <button
              type="button"
              onClick={() => setCreateOpen(true)}
              className={buttonClass('primary', 'text-xs')}
            >
              <Plus className="h-4 w-4" aria-hidden="true" />
              Create Problem
            </button>
          ) : null
        }
      />

      {/* Filters --------------------------------------------------------- */}
      <div className="flex flex-wrap items-center gap-2 border-b border-slate-800 px-4 py-2.5">
        <label className="relative flex min-w-[12rem] flex-1 items-center sm:max-w-xs">
          <Search className="pointer-events-none absolute left-2.5 h-3.5 w-3.5 text-slate-500" aria-hidden="true" />
          <input
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            placeholder="Filter by title or teacher"
            aria-label="Filter problems by title"
            className="w-full rounded-lg border border-slate-700 bg-slate-900/80 py-1.5 pl-8 pr-2 text-xs text-slate-100 placeholder:text-slate-500 focus:border-sky-500 focus:outline-none"
          />
        </label>

        <Segmented
          options={DIFFICULTY_OPTIONS}
          value={difficulty}
          onChange={setDifficulty}
          ariaLabel="Filter by difficulty"
          size="sm"
        />

        <select
          value={classroomFilter}
          onChange={(event) => setClassroomFilter(event.target.value)}
          aria-label="Filter by classroom"
          className="rounded-lg border border-slate-700 bg-slate-900/80 px-2 py-1.5 text-xs text-slate-200 focus:border-sky-500 focus:outline-none"
        >
          <option value="all">Every class</option>
          {staff ? <option value="private">My private bank</option> : null}
          {classrooms.map((classroom) => (
            <option key={classroom.id} value={String(classroom.id)}>
              {classroom.code} · {classroom.name}
            </option>
          ))}
        </select>

        {staff ? (
          <label className="inline-flex cursor-pointer items-center gap-1.5 rounded-lg border border-slate-700 bg-slate-900/80 px-2 py-1.5 text-xs text-slate-300">
            <input
              type="checkbox"
              checked={mineOnly}
              onChange={(event) => setMineOnly(event.target.checked)}
              className="h-3.5 w-3.5 accent-sky-500"
            />
            Only my problems
          </label>
        ) : null}

        <button
          type="button"
          onClick={list.reload}
          disabled={list.loading}
          title="Reload the list"
          className={buttonClass('ghost', 'px-2 py-1.5 text-xs')}
        >
          <RefreshCw className={`h-3.5 w-3.5 ${list.loading ? 'animate-spin' : ''}`} aria-hidden="true" />
          <span className="hidden sm:inline">Refresh</span>
        </button>

        <span className="ml-auto font-mono text-[11px] text-slate-500">
          {groupThousands(total)} problem{total === 1 ? '' : 's'}
        </span>
      </div>

      {/* List ------------------------------------------------------------ */}
      <div className="min-h-0 flex-1 overflow-y-auto p-4">
        {error ? (
          <div className="mb-3">
            <ErrorNotice message={error} onRetry={list.reload} />
          </div>
        ) : null}

        {list.loading && rows.length === 0 ? (
          <LoadingRow label="Loading problems…" />
        ) : visible.length === 0 ? (
          <EmptyState
            icon={<BookOpen className="h-8 w-8" aria-hidden="true" />}
            title={staff ? 'No problems match these filters' : 'Nothing published to your classes yet'}
            hint={
              staff
                ? 'Clear the filters, or publish a new exercise to a class so your students can see it.'
                : 'Your teacher publishes exercises to a class; join one with its invite code under Classrooms.'
            }
            action={
              staff ? (
                <button
                  type="button"
                  onClick={() => setCreateOpen(true)}
                  className={buttonClass('primary', 'text-xs')}
                >
                  <Plus className="h-4 w-4" aria-hidden="true" />
                  Create Problem
                </button>
              ) : null
            }
          />
        ) : (
          <ul className="grid gap-2.5 lg:grid-cols-2 2xl:grid-cols-3">
            {visible.map((problem) => (
              <li key={problem.id}>
                <button
                  type="button"
                  onClick={() => onOpen(problem.id)}
                  className="group flex h-full w-full flex-col gap-2 rounded-xl border border-slate-800 bg-slate-900/40 p-3 text-left transition-colors hover:border-sky-500/60 hover:bg-slate-900/70 focus:outline-none focus-visible:ring-2 focus-visible:ring-sky-400"
                >
                  <div className="flex items-start gap-2">
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-sm font-semibold text-slate-100 group-hover:text-sky-200">
                        {problem.title}
                      </span>
                      <span className="mt-0.5 block truncate text-[11px] text-slate-500">
                        by {problem.teacher_name || 'staff'} · #{problem.id} ·{' '}
                        {formatRelative(problem.created_at)}
                      </span>
                    </span>
                    <DifficultyBadge difficulty={problem.difficulty} />
                  </div>

                  <div className="flex flex-wrap items-center gap-1.5">
                    <MetaChip icon={<Layers className="h-3 w-3" aria-hidden="true" />}>
                      {problem.sample_count} sample
                    </MetaChip>
                    <MetaChip icon={<Lock className="h-3 w-3" aria-hidden="true" />}>
                      {problem.hidden_count} hidden
                    </MetaChip>
                    <MetaChip icon={<Clock className="h-3 w-3" aria-hidden="true" />}>
                      {formatSeconds(problem.time_limit_sec)}
                    </MetaChip>
                    <MetaChip icon={<Gauge className="h-3 w-3" aria-hidden="true" />}>
                      {problem.memory_limit_mb}MB
                    </MetaChip>
                    {problem.classroom_id === null ? (
                      <MetaChip icon={<Sparkles className="h-3 w-3" aria-hidden="true" />}>
                        private
                      </MetaChip>
                    ) : (
                      <MetaChip icon={<School className="h-3 w-3" aria-hidden="true" />}>
                        class #{problem.classroom_id}
                      </MetaChip>
                    )}
                  </div>
                </button>
              </li>
            ))}
          </ul>
        )}

        {!list.loading && rows.length < total ? (
          <div className="mt-4 flex justify-center">
            <button
              type="button"
              onClick={() => void loadMore()}
              disabled={loadingMore}
              className={buttonClass('ghost', 'text-xs')}
            >
              {loadingMore ? <Spinner /> : null}
              <span>
                Load more ({groupThousands(rows.length)} of {groupThousands(total)})
              </span>
            </button>
          </div>
        ) : null}
      </div>

      <ProblemCreateModal
        open={createOpen}
        onClose={() => setCreateOpen(false)}
        classrooms={classrooms}
        defaultClassroomId={/^[1-9]\d*$/.test(classroomFilter) ? Number(classroomFilter) : null}
        onCreated={() => {
          setCreateOpen(false)
          // Refetch page 1: the new problem may sort into any filter, and the
          // server stays the single source of truth for what the caller may see.
          list.reload()
        }}
      />
    </div>
  )
}

export default ProblemBank
