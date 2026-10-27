/**
 * Tab 3 - Classrooms.
 *
 * Teachers see the classes they own with their invite code, the roster and the
 * problems published there; students see the classes they joined and can enter an
 * invite code to join another. Admins see every class on the instance, which is
 * what `GET /api/classrooms` already scopes per role - the UI never asks for more.
 */
import { useState } from 'react'
import {
  BookOpen,
  ChevronDown,
  ChevronRight,
  ClipboardList,
  Copy,
  Download,
  Eye,
  Hash,
  GraduationCap,
  Plus,
  RefreshCw,
  School,
  ShieldAlert,
  Users,
} from 'lucide-react'

import SectionHeading from './SectionHeading'
import PlagiarismModal from './PlagiarismModal'
import ExamProctorModal from './ExamProctorModal'
import { DifficultyBadge, EmptyState, ErrorNotice, Field, LoadingRow, MetaChip, Modal, Spinner } from './ui'
import { useAuth } from '../authContext'
import api, { ApiError, saveBlob } from '../api'
import { useAsync } from '../useAsync'
import { INPUT_CLASS, buttonClass, formatRelative, groupThousands } from '../format'
import type { Classroom, ClassroomDetail } from '../types'

export interface ClassroomsProps {
  /** Jump straight into a problem from a class card. */
  onOpenProblem: (problemId: number) => void
}

/** Expanded class detail card (roster + problems), fetched lazily per class. */
function ClassroomCard({
  classroom,
  canManage,
  canExport,
  onOpenProblem,
}: {
  classroom: Classroom
  canManage: boolean
  /** Staff who may pull the whole class's marks (owner or admin). */
  canExport: boolean
  onOpenProblem: (problemId: number) => void
}) {
  const [open, setOpen] = useState(false)
  const [detail, setDetail] = useState<ClassroomDetail | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [copied, setCopied] = useState(false)
  const [exporting, setExporting] = useState(false)
  const [exportError, setExportError] = useState<string | null>(null)
  const [auditProblem, setAuditProblem] = useState<{ id: number; title: string } | null>(null)
  const [proctorProblem, setProctorProblem] = useState<{ id: number; title: string } | null>(null)

  const toggle = async () => {
    const next = !open
    setOpen(next)
    if (!next || detail) return
    setLoading(true)
    setError(null)
    try {
      setDetail(await api.getClassroom(classroom.id))
    } catch (reason) {
      setError(reason instanceof ApiError ? reason.detail : String(reason))
    } finally {
      setLoading(false)
    }
  }

  const copyCode = async () => {
    try {
      if (navigator.clipboard) await navigator.clipboard.writeText(classroom.code)
      setCopied(true)
      window.setTimeout(() => setCopied(false), 1400)
    } catch {
      setCopied(false)
    }
  }

  /**
   * Pull the server-side gradebook and save it as `gradebook_<code>.csv`.
   *
   * The CSV is built on the server (best attempt per problem, all attempts
   * counted) and is UTF-8 with a BOM, so Excel opens the Chinese titles without
   * an import wizard. The bytes never go through JSON parsing here.
   */
  const exportGradebook = async () => {
    if (exporting) return
    setExporting(true)
    setExportError(null)
    try {
      const blob = await api.downloadGradebookCsv(classroom.id)
      saveBlob(blob, `gradebook_${classroom.code}.csv`)
    } catch (reason) {
      setExportError(reason instanceof ApiError ? reason.detail : String(reason))
    } finally {
      setExporting(false)
    }
  }

  return (
    <li className="overflow-hidden rounded-xl border border-slate-800 bg-slate-900/40">
      <div className="flex flex-wrap items-start gap-x-3 gap-y-2 p-3">
        <button
          type="button"
          onClick={() => void toggle()}
          aria-expanded={open}
          className="flex min-w-0 flex-1 items-start gap-2 text-left"
        >
          {open ? (
            <ChevronDown className="mt-0.5 h-4 w-4 shrink-0 text-slate-500" aria-hidden="true" />
          ) : (
            <ChevronRight className="mt-0.5 h-4 w-4 shrink-0 text-slate-500" aria-hidden="true" />
          )}
          <span className="min-w-0 flex-1">
            <span className="flex flex-wrap items-center gap-2">
              <span className="truncate text-sm font-semibold text-slate-100">{classroom.name}</span>
              <button
                type="button"
                onClick={(event) => {
                  event.stopPropagation()
                  void copyCode()
                }}
                title="Copy the invite code"
                className="inline-flex items-center gap-1.5 rounded-md border border-sky-500/40 bg-sky-500/10 px-1.5 py-0.5 font-mono text-[11px] font-semibold text-sky-300 transition-colors hover:bg-sky-500/20"
              >
                <Hash className="h-3 w-3" aria-hidden="true" />
                {classroom.code}
                {copied ? (
                  <span className="text-emerald-300">copied</span>
                ) : (
                  <Copy className="h-3 w-3" aria-hidden="true" />
                )}
              </button>
            </span>
            {classroom.description ? (
              <span className="mt-1 block text-[11px] leading-relaxed text-slate-400">
                {classroom.description}
              </span>
            ) : null}
            <span className="mt-1.5 flex flex-wrap items-center gap-1.5">
              <MetaChip icon={<GraduationCap className="h-3 w-3" aria-hidden="true" />}>
                {classroom.student_count} student{classroom.student_count === 1 ? '' : 's'}
              </MetaChip>
              <MetaChip icon={<BookOpen className="h-3 w-3" aria-hidden="true" />}>
                {classroom.problem_count} problem{classroom.problem_count === 1 ? '' : 's'}
              </MetaChip>
              <MetaChip>
                {canManage ? 'you teach' : `taught by ${classroom.teacher_name || 'staff'}`}
              </MetaChip>
              <span className="font-mono text-[10px] text-slate-600">
                created {formatRelative(classroom.created_at)}
              </span>
            </span>
          </span>
        </button>
      </div>

      {open ? (
        <div className="border-t border-slate-800 bg-slate-950/40 px-3 py-3">
          {canExport ? (
            <div className="mb-3 flex flex-wrap items-center gap-2">
              <button
                type="button"
                onClick={() => void exportGradebook()}
                disabled={exporting}
                title="Every enrolled student x every problem of this class, best attempt per problem"
                className={buttonClass('ghost', 'px-2 py-1.5 text-xs')}
              >
                {exporting ? <Spinner className="h-3.5 w-3.5" /> : <Download className="h-3.5 w-3.5" aria-hidden="true" />}
                <span>匯出成績冊 (CSV)</span>
              </button>
              <span className="font-mono text-[10px] text-slate-600">
                gradebook_{classroom.code}.csv
              </span>
              {exportError ? (
                <span className="w-full text-[11px] text-rose-300">{exportError}</span>
              ) : null}
            </div>
          ) : null}

          {loading ? <LoadingRow label="Loading roster…" /> : null}
          {error ? <ErrorNotice message={error} onRetry={() => void toggle()} /> : null}

          {detail ? (
            <div className="grid gap-3 lg:grid-cols-2">
              <div>
                <p className="mb-1.5 flex items-center gap-2 text-[11px] font-semibold uppercase tracking-wider text-slate-500">
                  <Users className="h-3.5 w-3.5 text-sky-400" aria-hidden="true" />
                  Enrolled students ({detail.students.length})
                </p>
                {detail.students.length === 0 ? (
                  <p className="rounded-lg border border-dashed border-slate-700 px-3 py-2 text-[11px] text-slate-500">
                    Nobody has joined yet. Share the code{' '}
                    <span className="font-mono text-sky-300">{detail.code}</span> with your class.
                  </p>
                ) : (
                  <ul className="divide-y divide-slate-800 overflow-hidden rounded-lg border border-slate-800">
                    {detail.students.map((student) => (
                      <li
                        key={student.id}
                        className="flex items-center gap-2 bg-slate-900/40 px-2.5 py-1.5 text-[11px]"
                      >
                        <span className="min-w-0 flex-1">
                          <span className="block truncate font-semibold text-slate-200">
                            {student.full_name || student.username}
                          </span>
                          <span className="block truncate font-mono text-[10px] text-slate-500">
                            {student.username} · {student.email}
                          </span>
                        </span>
                        <span className="font-mono text-[10px] text-slate-600">
                          joined {formatRelative(student.joined_at)}
                        </span>
                      </li>
                    ))}
                  </ul>
                )}
              </div>

              <div>
                <p className="mb-1.5 flex items-center gap-2 text-[11px] font-semibold uppercase tracking-wider text-slate-500">
                  <ClipboardList className="h-3.5 w-3.5 text-emerald-400" aria-hidden="true" />
                  Problems published here ({detail.problems.length})
                </p>
                {detail.problems.length === 0 ? (
                  <p className="rounded-lg border border-dashed border-slate-700 px-3 py-2 text-[11px] text-slate-500">
                    No exercises published to this class yet.
                  </p>
                ) : (
                  <ul className="space-y-1.5">
                    {detail.problems.map((problem) => (
                      <li key={problem.id} className="flex items-center gap-2">
                        <button
                          type="button"
                          onClick={() => onOpenProblem(problem.id)}
                          className="flex w-full items-center gap-2 rounded-lg border border-slate-800 bg-slate-900/40 px-2.5 py-1.5 text-left transition-colors hover:border-sky-500/60 hover:bg-slate-900 min-w-0 flex-1"
                        >
                          <span className="min-w-0 flex-1 truncate text-[11px] font-semibold text-slate-200">
                            {problem.title}
                          </span>
                          <DifficultyBadge difficulty={problem.difficulty} />
                          <span className="font-mono text-[10px] text-slate-500 shrink-0">
                            {problem.sample_count + problem.hidden_count} cases
                          </span>
                        </button>
                        {canManage ? (
                          <div className="flex items-center gap-1 shrink-0">
                            <button
                              type="button"
                              onClick={(e) => {
                                e.stopPropagation()
                                setProctorProblem({ id: problem.id, title: problem.title })
                              }}
                              title="即時監考儀表板 (Live Exam Proctoring)"
                              className="p-1.5 rounded-lg border border-slate-800 hover:border-amber-500/40 hover:bg-amber-500/10 text-slate-400 hover:text-amber-300 transition"
                            >
                              <Eye className="w-3.5 h-3.5" />
                            </button>
                            <button
                              type="button"
                              onClick={(e) => {
                                e.stopPropagation()
                                setAuditProblem({ id: problem.id, title: problem.title })
                              }}
                              title="查重審查 (Plagiarism Screen)"
                              className="p-1.5 rounded-lg border border-slate-800 hover:border-rose-500/40 hover:bg-rose-500/10 text-slate-400 hover:text-rose-300 transition"
                            >
                              <ShieldAlert className="w-3.5 h-3.5" />
                            </button>
                          </div>
                        ) : null}
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            </div>
          ) : null}

          {auditProblem ? (
            <PlagiarismModal
              open={Boolean(auditProblem)}
              onClose={() => setAuditProblem(null)}
              classroomId={classroom.id}
              classroomName={classroom.name}
              problemId={auditProblem.id}
              problemTitle={auditProblem.title}
            />
          ) : null}

          {proctorProblem ? (
            <ExamProctorModal
              open={Boolean(proctorProblem)}
              onClose={() => setProctorProblem(null)}
              problemId={proctorProblem.id}
            />
          ) : null}
        </div>
      ) : null}
    </li>
  )
}

export function Classrooms({ onOpenProblem }: ClassroomsProps) {
  const { user } = useAuth()
  const staff = user?.role === 'teacher' || user?.role === 'admin'

  const [createOpen, setCreateOpen] = useState(false)
  const [joinOpen, setJoinOpen] = useState(false)
  /** Locally known rows the server snapshot does not have yet (just created/joined). */
  const [injected, setInjected] = useState<{ token: string; rows: Classroom[] } | null>(null)

  const list = useAsync(`classrooms:${user?.id ?? 'guest'}`, () => api.listClassrooms())
  const loading = list.loading
  const error = list.error

  const remember = (classroom: Classroom) => {
    setInjected((previous) => {
      const known = previous && previous.token === list.token ? previous.rows : []
      if (known.some((row) => row.id === classroom.id)) return previous
      return { token: list.token, rows: [classroom, ...known] }
    })
  }

  const extraRows = injected && injected.token === list.token ? injected.rows : []
  const serverRows = (list.data ?? []).filter(
    (classroom) => !extraRows.some((row) => row.id === classroom.id),
  )
  const classrooms = [...extraRows, ...serverRows]

  return (
    <div className="flex min-h-0 flex-1 flex-col overflow-hidden">
      <SectionHeading
        title={staff ? 'My Classrooms' : 'My Classes'}
        hint={
          staff
            ? 'Every class you own, with its invite code, roster and published exercises.'
            : 'Classes you have joined. Ask your teacher for an invite code to join another.'
        }
        icon={<School className="h-4 w-4" aria-hidden="true" />}
        actions={
          <>
            <button
              type="button"
              onClick={list.reload}
              disabled={loading}
              className={buttonClass('ghost', 'px-2 py-1.5 text-xs')}
            >
              <RefreshCw className={`h-3.5 w-3.5 ${loading ? 'animate-spin' : ''}`} aria-hidden="true" />
              <span className="hidden sm:inline">Refresh</span>
            </button>
            {staff ? (
              <button
                type="button"
                onClick={() => setCreateOpen(true)}
                className={buttonClass('primary', 'text-xs')}
              >
                <Plus className="h-4 w-4" aria-hidden="true" />
                Create Classroom
              </button>
            ) : (
              <button
                type="button"
                onClick={() => setJoinOpen(true)}
                className={buttonClass('primary', 'text-xs')}
              >
                <Users className="h-4 w-4" aria-hidden="true" />
                Join Classroom
              </button>
            )}
          </>
        }
      />

      <div className="min-h-0 flex-1 overflow-y-auto p-4">
        {error ? (
          <div className="mb-3">
            <ErrorNotice message={error} onRetry={list.reload} />
          </div>
        ) : null}

        {loading ? (
          <LoadingRow label="Loading classrooms…" />
        ) : classrooms.length === 0 ? (
          <EmptyState
            icon={<School className="h-8 w-8" aria-hidden="true" />}
            title={staff ? 'You do not teach a class yet' : 'You have not joined a class yet'}
            hint={
              staff
                ? 'Create one and share the invite code; students who join instantly see the problems you publish there.'
                : 'Join with the invite code your teacher gave you - graded problems only appear once you are enrolled.'
            }
            action={
              staff ? (
                <button
                  type="button"
                  onClick={() => setCreateOpen(true)}
                  className={buttonClass('primary', 'text-xs')}
                >
                  <Plus className="h-4 w-4" aria-hidden="true" />
                  Create Classroom
                </button>
              ) : (
                <button
                  type="button"
                  onClick={() => setJoinOpen(true)}
                  className={buttonClass('primary', 'text-xs')}
                >
                  <Users className="h-4 w-4" aria-hidden="true" />
                  Join Classroom
                </button>
              )
            }
          />
        ) : (
          <ul className="space-y-2.5">
            {classrooms.map((classroom) => (
              <ClassroomCard
                key={classroom.id}
                classroom={classroom}
                canManage={Boolean(user && classroom.teacher_id === user.id)}
                canExport={Boolean(
                  user && (user.role === 'admin' || classroom.teacher_id === user.id),
                )}
                onOpenProblem={onOpenProblem}
              />
            ))}
          </ul>
        )}

        {!loading && classrooms.length > 0 ? (
          <p className="mt-3 text-center font-mono text-[10px] text-slate-600">
            {groupThousands(classrooms.length)} classroom{classrooms.length === 1 ? '' : 's'} visible to{' '}
            {user?.role}
          </p>
        ) : null}
      </div>

      {staff ? (
        <CreateClassroomModal
          open={createOpen}
          onClose={() => setCreateOpen(false)}
          onCreated={(classroom) => {
            setCreateOpen(false)
            remember(classroom)
          }}
        />
      ) : (
        <JoinClassroomModal
          open={joinOpen}
          onClose={() => setJoinOpen(false)}
          onJoined={(classroom) => {
            setJoinOpen(false)
            remember(classroom)
          }}
        />
      )}
    </div>
  )
}

/* -------------------------------------------------------------------------- */

function CreateClassroomModal({
  open,
  onClose,
  onCreated,
}: {
  open: boolean
  onClose: () => void
  onCreated: (classroom: Classroom) => void
}) {
  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  const [code, setCode] = useState('')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const save = async () => {
    if (saving) return
    setError(null)
    if (name.trim().length < 2) {
      setError('The class name needs at least 2 characters.')
      return
    }
    setSaving(true)
    try {
      const created = await api.createClassroom({
        name: name.trim(),
        description,
        code: code.trim() === '' ? null : code.trim().toUpperCase(),
      })
      onCreated(created)
      setName('')
      setDescription('')
      setCode('')
    } catch (reason) {
      setError(reason instanceof ApiError ? reason.detail : String(reason))
    } finally {
      setSaving(false)
    }
  }

  return (
    <Modal
      open={open}
      onClose={() => {
        if (!saving) onClose()
      }}
      title="Create a classroom"
      subtitle="Students enrol with the invite code; problems you publish become visible to them."
      icon={<School className="h-5 w-5" aria-hidden="true" />}
      footer={
        <>
          <button type="button" onClick={onClose} className={buttonClass('ghost')}>
            Cancel
          </button>
          <button
            type="button"
            onClick={() => void save()}
            disabled={saving}
            className={buttonClass('primary')}
          >
            {saving ? <Spinner /> : <Plus className="h-4 w-4" aria-hidden="true" />}
            <span>{saving ? 'Creating…' : 'Create classroom'}</span>
          </button>
        </>
      }
    >
      <div className="space-y-3">
        {error ? <ErrorNotice message={error} /> : null}
        <Field label="Class name" hint="min 2 chars" htmlFor="class-name">
          <input
            id="class-name"
            value={name}
            onChange={(event) => setName(event.target.value)}
            placeholder="Data Structures 202"
            className={INPUT_CLASS}
          />
        </Field>
        <Field label="Description" hint="optional" htmlFor="class-desc">
          <textarea
            id="class-desc"
            value={description}
            onChange={(event) => setDescription(event.target.value)}
            rows={3}
            placeholder="Weekly judge exercises, due Fridays."
            className={INPUT_CLASS}
          />
        </Field>
        <Field
          label="Invite code"
          hint="A-Z 0-9 _ - · leave empty to auto-generate"
          htmlFor="class-code"
        >
          <input
            id="class-code"
            value={code}
            onChange={(event) => setCode(event.target.value.toUpperCase())}
            placeholder="CS101"
            maxLength={16}
            className={`${INPUT_CLASS} font-mono uppercase`}
          />
        </Field>
      </div>
    </Modal>
  )
}

function JoinClassroomModal({
  open,
  onClose,
  onJoined,
}: {
  open: boolean
  onClose: () => void
  onJoined: (classroom: Classroom) => void
}) {
  const [code, setCode] = useState('')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const join = async () => {
    if (saving) return
    setError(null)
    if (code.trim().length < 3) {
      setError('Invite codes are at least 3 characters.')
      return
    }
    setSaving(true)
    try {
      const detail = await api.joinClassroom(code.trim().toUpperCase())
      onJoined(detail)
      setCode('')
    } catch (reason) {
      setError(reason instanceof ApiError ? reason.detail : String(reason))
    } finally {
      setSaving(false)
    }
  }

  return (
    <Modal
      open={open}
      onClose={() => {
        if (!saving) onClose()
      }}
      title="Join a classroom"
      subtitle="Type the invite code your teacher shared. Codes are case-insensitive."
      icon={<Users className="h-5 w-5" aria-hidden="true" />}
      footer={
        <>
          <button type="button" onClick={onClose} className={buttonClass('ghost')}>
            Cancel
          </button>
          <button
            type="button"
            onClick={() => void join()}
            disabled={saving}
            className={buttonClass('primary')}
          >
            {saving ? <Spinner /> : <Users className="h-4 w-4" aria-hidden="true" />}
            <span>{saving ? 'Joining…' : 'Join classroom'}</span>
          </button>
        </>
      }
    >
      <div className="space-y-3">
        {error ? <ErrorNotice message={error} /> : null}
        <Field label="Invite code" htmlFor="join-code">
          <input
            id="join-code"
            value={code}
            onChange={(event) => setCode(event.target.value.toUpperCase())}
            onKeyDown={(event) => {
              if (event.key === 'Enter') void join()
            }}
            placeholder="CS101"
            autoFocus
            maxLength={16}
            className={`${INPUT_CLASS} font-mono text-base uppercase tracking-[0.2em]`}
          />
        </Field>
        <p className="text-[11px] text-slate-500">
          Joining is instant and repeat-safe: joining twice answers “already enrolled”.
        </p>
      </div>
    </Modal>
  )
}

export default Classrooms
