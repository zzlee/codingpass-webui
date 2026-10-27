/**
 * Tab 4 - submission history.
 *
 * Scope comes from the API, not from this component: a student only ever gets
 * their own rows, a teacher gets attempts on their problems plus attempts by
 * students of their classes (narrowable with `classroom_id` / `student_id`), and
 * an admin sees everything. Verdicts are shown with the same badges as the judge
 * result panel so a teacher's gradebook reads like the student's own screen.
 */
import { useMemo, useState } from 'react'
import {
  ChartColumn,
  Clock,
  Filter,
  RefreshCw,
  School,
  Trophy,
  User,
} from 'lucide-react'

import SectionHeading from './SectionHeading'
import { EmptyState, ErrorNotice, LoadingRow, Spinner, VerdictBadge } from './ui'
import { useAuth } from '../authContext'
import api from '../api'
import { describeError, useAsync } from '../useAsync'
import { buttonClass, formatMs, formatScore, formatTimestamp, groupThousands } from '../format'
import type { Classroom, SubmissionRow, Verdict } from '../types'

const PAGE_SIZE = 25

const STATUS_OPTIONS: Array<{ value: Verdict | 'all'; label: string }> = [
  { value: 'all', label: 'All verdicts' },
  { value: 'accepted', label: 'Accepted' },
  { value: 'wrong_answer', label: 'Wrong answer' },
  { value: 'time_limit_exceeded', label: 'Time limit' },
  { value: 'compile_error', label: 'Compile error' },
  { value: 'runtime_error', label: 'Runtime error' },
]

export function Submissions() {
  const { user } = useAuth()
  const staff = user?.role === 'teacher' || user?.role === 'admin'

  const [status, setStatus] = useState<Verdict | 'all'>('all')
  const [classroomFilter, setClassroomFilter] = useState('all')
  const [studentFilter, setStudentFilter] = useState('all')
  /** Appended pages, tagged with the request they belong to. */
  const [extra, setExtra] = useState<{ token: string; rows: SubmissionRow[]; total: number } | null>(
    null,
  )
  const [loadingMore, setLoadingMore] = useState(false)
  const [moreError, setMoreError] = useState<string | null>(null)

  const query = useMemo(
    () => ({
      status: status === 'all' ? ('' as const) : status,
      classroom_id: /^[1-9]\d*$/.test(classroomFilter) ? Number(classroomFilter) : undefined,
      student_id: /^[1-9]\d*$/.test(studentFilter) ? Number(studentFilter) : undefined,
    }),
    [status, classroomFilter, studentFilter],
  )
  const filterKey = JSON.stringify(query)

  const list = useAsync(`submissions:${user?.id ?? 'guest'}:${filterKey}`, () =>
    api.listSubmissions({ ...query, limit: PAGE_SIZE, offset: 0 }),
  )
  const classroomList = useAsync(`classrooms:${user?.id ?? 'guest'}`, () =>
    staff ? api.listClassrooms() : Promise.resolve([] as Classroom[]),
  )

  const classrooms = classroomList.data ?? []
  const appended = extra && extra.token === list.token ? extra.rows : []
  const rows = [...(list.data?.submissions ?? []), ...appended]
  const total = extra && extra.token === list.token ? extra.total : (list.data?.total ?? 0)
  const loading = list.loading
  const error = list.error ?? moreError

  /** Roster of the selected class, feeding the student filter. */
  const selectedClassroomId = /^[1-9]\d*$/.test(classroomFilter) ? Number(classroomFilter) : null
  const rosterQuery = useAsync(
    staff && selectedClassroomId !== null ? `roster:${selectedClassroomId}` : 'roster:none',
    () => (selectedClassroomId === null ? Promise.resolve(null) : api.getClassroom(selectedClassroomId)),
  )
  const roster = rosterQuery.data?.students ?? []

  const loadMore = async () => {
    if (loadingMore || rows.length >= total) return
    setLoadingMore(true)
    setMoreError(null)
    try {
      const reply = await api.listSubmissions({ ...query, limit: PAGE_SIZE, offset: rows.length })
      setExtra((previous) =>
        previous && previous.token === list.token
          ? { token: list.token, rows: [...previous.rows, ...reply.submissions], total: reply.total }
          : { token: list.token, rows: reply.submissions, total: reply.total },
      )
    } catch (reason) {
      setMoreError(describeError(reason))
    } finally {
      setLoadingMore(false)
    }
  }

  const accepted = rows.filter((row) => row.status === 'accepted').length

  return (
    <div className="flex min-h-0 flex-1 flex-col overflow-hidden">
      <SectionHeading
        title={staff ? 'Class Submissions' : 'My Submissions'}
        hint={
          staff
            ? 'Latest attempts on your problems and your students’ attempts across your classes.'
            : 'Every attempt you submitted, newest first. Only your own rows are visible to you.'
        }
        icon={<ChartColumn className="h-4 w-4" aria-hidden="true" />}
        actions={
          <button
            type="button"
            onClick={list.reload}
            disabled={loading}
            className={buttonClass('ghost', 'px-2 py-1.5 text-xs')}
          >
            <RefreshCw className={`h-3.5 w-3.5 ${loading ? 'animate-spin' : ''}`} aria-hidden="true" />
            <span className="hidden sm:inline">Refresh</span>
          </button>
        }
      />

      {/* Filters --------------------------------------------------------- */}
      <div className="flex flex-wrap items-center gap-2 border-b border-slate-800 px-4 py-2.5">
        <span className="inline-flex items-center gap-1.5 text-[11px] uppercase tracking-wider text-slate-500">
          <Filter className="h-3.5 w-3.5" aria-hidden="true" />
          Filters
        </span>
        <select
          value={status}
          onChange={(event) => setStatus(event.target.value as Verdict | 'all')}
          aria-label="Filter by verdict"
          className="rounded-lg border border-slate-700 bg-slate-900/80 px-2 py-1.5 text-xs text-slate-200 focus:border-sky-500 focus:outline-none"
        >
          {STATUS_OPTIONS.map((option) => (
            <option key={option.value} value={option.value}>
              {option.label}
            </option>
          ))}
        </select>

        {staff ? (
          <select
            value={classroomFilter}
            onChange={(event) => {
              setClassroomFilter(event.target.value)
              setStudentFilter('all')
            }}
            aria-label="Filter by classroom"
            className="rounded-lg border border-slate-700 bg-slate-900/80 px-2 py-1.5 text-xs text-slate-200 focus:border-sky-500 focus:outline-none"
          >
            <option value="all">Every class</option>
            {classrooms.map((classroom) => (
              <option key={classroom.id} value={String(classroom.id)}>
                {classroom.code} · {classroom.name}
              </option>
            ))}
          </select>
        ) : null}

        {staff && roster.length > 0 ? (
          <select
            value={studentFilter}
            onChange={(event) => setStudentFilter(event.target.value)}
            aria-label="Filter by student"
            className="rounded-lg border border-slate-700 bg-slate-900/80 px-2 py-1.5 text-xs text-slate-200 focus:border-sky-500 focus:outline-none"
          >
            <option value="all">Every student</option>
            {roster.map((student) => (
              <option key={student.id} value={String(student.id)}>
                {student.full_name || student.username} ({student.username})
              </option>
            ))}
          </select>
        ) : null}

        <span className="ml-auto flex items-center gap-2 font-mono text-[11px] text-slate-500">
          {rows.length > 0 ? (
            <span className="text-emerald-300">
              {groupThousands(accepted)} accepted
            </span>
          ) : null}
          {groupThousands(total)} attempt{total === 1 ? '' : 's'}
        </span>
      </div>

      {/* Table ----------------------------------------------------------- */}
      <div className="min-h-0 flex-1 overflow-auto p-4">
        {error ? (
          <div className="mb-3">
            <ErrorNotice message={error} onRetry={list.reload} />
          </div>
        ) : null}

        {loading ? (
          <LoadingRow label="Loading submissions…" />
        ) : rows.length === 0 ? (
          <EmptyState
            icon={<Trophy className="h-8 w-8" aria-hidden="true" />}
            title="No submissions yet"
            hint={
              staff
                ? 'Once your students submit, verdicts and scores land here. Your own test submissions are trial runs and are not stored.'
                : 'Open a problem from the Problem Bank and press Submit Solution - attempts appear here.'
            }
          />
        ) : (
          <div className="overflow-hidden rounded-xl border border-slate-800">
            <table className="w-full min-w-[46rem] border-collapse text-left text-xs">
              <thead>
                <tr className="bg-slate-900/80 text-[10px] uppercase tracking-wider text-slate-500">
                  <th className="px-3 py-2 font-semibold">Problem</th>
                  <th className="px-3 py-2 font-semibold">Student</th>
                  <th className="px-3 py-2 font-semibold">Lang</th>
                  <th className="px-3 py-2 font-semibold">Verdict</th>
                  <th className="px-3 py-2 text-right font-semibold">Score</th>
                  <th className="px-3 py-2 text-right font-semibold">Passed</th>
                  <th className="px-3 py-2 text-right font-semibold">Time</th>
                  <th className="px-3 py-2 text-right font-semibold">When</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-800">
                {rows.map((row) => (
                  <tr key={row.id} className="bg-slate-900/30 transition-colors hover:bg-slate-900/70">
                    <td className="max-w-[16rem] px-3 py-2">
                      <span className="block truncate font-semibold text-slate-200">
                        {row.problem_title || `Problem #${row.problem_id}`}
                      </span>
                      <span className="font-mono text-[10px] text-slate-600">
                        #{row.id} · problem #{row.problem_id}
                      </span>
                    </td>
                    <td className="px-3 py-2">
                      <span className="flex items-center gap-1.5">
                        <User className="h-3 w-3 text-slate-500" aria-hidden="true" />
                        <span className="min-w-0">
                          <span className="block truncate text-slate-300">
                            {row.student_name || row.student_username}
                          </span>
                          <span className="block truncate font-mono text-[10px] text-slate-600">
                            {row.student_username}
                          </span>
                        </span>
                      </span>
                    </td>
                    <td className="px-3 py-2">
                      <span className="rounded bg-slate-800 px-1.5 py-0.5 font-mono text-[10px] uppercase text-slate-300">
                        {row.language === 'cpp' ? 'C++' : 'C'}
                      </span>
                    </td>
                    <td className="px-3 py-2">
                      <VerdictBadge status={row.status} withEmoji={false} />
                    </td>
                    <td className="px-3 py-2 text-right font-mono text-slate-200">
                      {formatScore(row.score)}
                    </td>
                    <td className="px-3 py-2 text-right font-mono text-slate-400">
                      {row.passed_count}/{row.total_count}
                    </td>
                    <td className="px-3 py-2 text-right font-mono text-slate-400">
                      <span className="inline-flex items-center gap-1">
                        <Clock className="h-3 w-3 text-slate-600" aria-hidden="true" />
                        {formatMs(row.execution_time_ms)}
                      </span>
                    </td>
                    <td className="px-3 py-2 text-right font-mono text-[11px] text-slate-500">
                      {formatTimestamp(row.created_at)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        {!loading && rows.length < total ? (
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

        {!loading && rows.length > 0 ? (
          <p className="mt-3 text-center font-mono text-[10px] text-slate-600">
            <School className="mr-1 inline h-3 w-3" aria-hidden="true" />
            visibility follows your {user?.role} role - the API never returns rows you may not read
          </p>
        ) : null}
      </div>
    </div>
  )
}

export default Submissions
