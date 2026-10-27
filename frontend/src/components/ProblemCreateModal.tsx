/**
 * Teacher-only "Create Problem" dialog.
 *
 * Builds the `POST /api/problems` payload in one go: statement (Markdown, with a
 * live preview), limits, starter code for C and C++, and the test data - sample
 * cases students can see and run against, plus hidden cases used only for
 * grading. The API mirrors the same limits (`MAX_TEST_CASES`, time/memory
 * bounds), so the form enforces them before the request to avoid a 422 round
 * trip.
 */
import { useMemo, useState } from 'react'
import {
  BookOpen,
  Eye,
  FlaskConical,
  Info,
  Layers,
  Lock,
  Plus,
  Sparkles,
  Trash,
  TriangleAlert,
} from 'lucide-react'

import Markdown from './Markdown'
import { Field, InfoNotice, Modal, Spinner } from './ui'
import { INPUT_CLASS, buttonClass } from '../format'
import api, { ApiError } from '../api'
import type { Classroom, Difficulty, ProblemDetail, TestCaseDraft } from '../types'

/** Bounds kept in sync with `backend/main.py`. */
const LIMITS = {
  maxCases: 50,
  minTimeSec: 0.1,
  maxTimeSec: 20,
  minMemoryMb: 16,
  maxMemoryMb: 4096,
}

const DEFAULT_STARTER_C = `#include <stdio.h>

int main(void) {
    /* TODO: read the input, compute the answer, print it. */
    return 0;
}
`

const DEFAULT_STARTER_CPP = `#include <iostream>

int main() {
    // TODO: read the input, compute the answer, print it.
    return 0;
}
`

interface DraftCase extends TestCaseDraft {
  /** Local identity so removing a row never re-targets the wrong one. */
  key: number
}

let caseKeySeed = 0
const nextCaseKey = () => ++caseKeySeed

export interface ProblemCreateModalProps {
  open: boolean
  onClose: () => void
  /** Classrooms the teacher may publish to (own classes; `[]` = private bank only). */
  classrooms: Classroom[]
  /** Default class to publish to (the one currently filtered by, if any). */
  defaultClassroomId?: number | null
  onCreated: (problem: ProblemDetail) => void
}

export function ProblemCreateModal({
  open,
  onClose,
  classrooms,
  defaultClassroomId,
  onCreated,
}: ProblemCreateModalProps) {
  const [title, setTitle] = useState('')
  const [difficulty, setDifficulty] = useState<Difficulty>('easy')
  const [description, setDescription] = useState('')
  const [preview, setPreview] = useState(false)
  const [timeLimit, setTimeLimit] = useState('2')
  const [memoryMb, setMemoryMb] = useState('256')
  const [classroomId, setClassroomId] = useState<string>('')
  const [starterC, setStarterC] = useState(DEFAULT_STARTER_C)
  const [starterCpp, setStarterCpp] = useState(DEFAULT_STARTER_CPP)
  const [showStarter, setShowStarter] = useState(false)
  const [cases, setCases] = useState<DraftCase[]>([
    { key: nextCaseKey(), input: '', expected_output: '', is_sample: true },
    { key: nextCaseKey(), input: '', expected_output: '', is_sample: true },
    { key: nextCaseKey(), input: '', expected_output: '', is_sample: false },
  ])
  const [error, setError] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)

  const samples = useMemo(() => cases.filter((row) => row.is_sample), [cases])
  const hidden = useMemo(() => cases.filter((row) => !row.is_sample), [cases])

  const reset = () => {
    setTitle('')
    setDifficulty('easy')
    setDescription('')
    setPreview(false)
    setTimeLimit('2')
    setMemoryMb('256')
    setClassroomId(defaultClassroomId !== null && defaultClassroomId !== undefined ? String(defaultClassroomId) : '')
    setStarterC(DEFAULT_STARTER_C)
    setStarterCpp(DEFAULT_STARTER_CPP)
    setCases([
      { key: nextCaseKey(), input: '', expected_output: '', is_sample: true },
      { key: nextCaseKey(), input: '', expected_output: '', is_sample: true },
      { key: nextCaseKey(), input: '', expected_output: '', is_sample: false },
    ])
    setError(null)
  }

  const update = (key: number, patch: Partial<TestCaseDraft>) => {
    setCases((previous) => previous.map((row) => (row.key === key ? { ...row, ...patch } : row)))
  }

  const remove = (key: number) => {
    setCases((previous) => previous.filter((row) => row.key !== key))
  }

  const add = (isSample: boolean) => {
    if (cases.length >= LIMITS.maxCases) {
      setError(`A problem may carry at most ${LIMITS.maxCases} test cases.`)
      return
    }
    setCases((previous) => [
      ...previous,
      { key: nextCaseKey(), input: '', expected_output: '', is_sample: isSample },
    ])
  }

  const save = async () => {
    if (saving) return
    setError(null)

    if (title.trim().length < 3) {
      setError('The title needs at least 3 characters.')
      return
    }
    const seconds = Number(timeLimit)
    if (!Number.isFinite(seconds) || seconds < LIMITS.minTimeSec || seconds > LIMITS.maxTimeSec) {
      setError(`Time limit must be between ${LIMITS.minTimeSec}s and ${LIMITS.maxTimeSec}s.`)
      return
    }
    const memory = Number(memoryMb)
    if (!Number.isFinite(memory) || memory < LIMITS.minMemoryMb || memory > LIMITS.maxMemoryMb) {
      setError(`Memory limit must be between ${LIMITS.minMemoryMb}MB and ${LIMITS.maxMemoryMb}MB.`)
      return
    }
    const filled = cases.filter((row) => row.input.trim() !== '' || row.expected_output.trim() !== '')
    if (filled.length === 0) {
      setError('Add at least one test case - a problem without cases cannot be graded.')
      return
    }

    setSaving(true)
    try {
      const created = await api.createProblem({
        title: title.trim(),
        description,
        difficulty,
        time_limit_sec: seconds,
        memory_limit_mb: Math.round(memory),
        classroom_id: classroomId === '' ? null : Number(classroomId),
        starter_code_c: starterC,
        starter_code_cpp: starterCpp,
        test_cases: filled.map((row) => ({
          input: row.input,
          expected_output: row.expected_output,
          is_sample: row.is_sample,
        })),
      })
      onCreated(created)
      reset()
    } catch (reason) {
      setError(reason instanceof ApiError ? reason.detail : String(reason))
    } finally {
      setSaving(false)
    }
  }

  const renderCaseGroup = (rows: DraftCase[], isSample: boolean) => (
    <div className="space-y-2">
      {rows.length === 0 ? (
        <p className="rounded-lg border border-dashed border-slate-700 px-3 py-2 text-[11px] text-slate-500">
          No {isSample ? 'sample' : 'hidden'} cases yet.
        </p>
      ) : null}
      {rows.map((row, index) => (
        <div
          key={row.key}
          className="rounded-lg border border-slate-700/70 bg-slate-950/50 p-2.5"
        >
          <div className="mb-1.5 flex items-center gap-2">
            <span className="font-mono text-[10px] uppercase tracking-wider text-slate-500">
              {isSample ? `Sample #${index + 1}` : `Hidden #${index + 1}`}
            </span>
            <button
              type="button"
              onClick={() => update(row.key, { is_sample: !isSample })}
              title={`Move this case to the ${isSample ? 'hidden' : 'sample'} set`}
              className="rounded border border-slate-700 px-1.5 py-0.5 text-[10px] text-slate-400 transition-colors hover:border-sky-500/60 hover:text-sky-300"
            >
              {isSample ? 'make hidden' : 'make sample'}
            </button>
            <button
              type="button"
              onClick={() => remove(row.key)}
              title="Remove this test case"
              className="ml-auto rounded border border-slate-700 p-1 text-slate-400 transition-colors hover:border-rose-500/60 hover:text-rose-300"
            >
              <Trash className="h-3 w-3" aria-hidden="true" />
              <span className="sr-only">Remove test case</span>
            </button>
          </div>
          <div className="grid gap-2 md:grid-cols-2">
            <textarea
              value={row.input}
              onChange={(event) => update(row.key, { input: event.target.value })}
              placeholder="stdin"
              spellCheck={false}
              rows={3}
              className={`${INPUT_CLASS} font-mono text-[11.5px]`}
            />
            <textarea
              value={row.expected_output}
              onChange={(event) => update(row.key, { expected_output: event.target.value })}
              placeholder="expected stdout"
              spellCheck={false}
              rows={3}
              className={`${INPUT_CLASS} font-mono text-[11.5px]`}
            />
          </div>
        </div>
      ))}
      <button
        type="button"
        onClick={() => add(isSample)}
        className={buttonClass('ghost', 'w-full text-[11px]')}
      >
        <Plus className="h-3.5 w-3.5" aria-hidden="true" />
        Add {isSample ? 'sample' : 'hidden'} case
      </button>
    </div>
  )

  return (
    <Modal
      open={open}
      onClose={() => {
        if (!saving) onClose()
      }}
      wide
      title="Create a problem"
      subtitle="Statement, starter code and the test data used to grade it."
      icon={<Sparkles className="h-5 w-5" aria-hidden="true" />}
      footer={
        <>
          <span className="mr-auto font-mono text-[10px] text-slate-500">
            {samples.length} sample · {hidden.length} hidden · {cases.length} / {LIMITS.maxCases} total
          </span>
          <button
            type="button"
            onClick={() => {
              if (!saving) {
                reset()
                onClose()
              }
            }}
            className={buttonClass('ghost')}
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={() => void save()}
            disabled={saving}
            className={buttonClass('primary')}
          >
            {saving ? <Spinner /> : <FlaskConical className="h-4 w-4" aria-hidden="true" />}
            <span>{saving ? 'Publishing…' : 'Publish problem'}</span>
          </button>
        </>
      }
    >
      <div className="space-y-3">
        {error ? (
          <p
            role="alert"
            className="flex items-start gap-2 rounded-lg border border-rose-500/40 bg-rose-500/10 px-3 py-2 text-xs text-rose-200"
          >
            <TriangleAlert className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
            <span className="min-w-0 break-words">{error}</span>
          </p>
        ) : null}

        <div className="grid gap-3 lg:grid-cols-[minmax(0,1fr)_11rem]">
          <Field label="Title" hint="min 3 chars" htmlFor="problem-title">
            <input
              id="problem-title"
              value={title}
              onChange={(event) => setTitle(event.target.value)}
              placeholder="Sum of an Array"
              className={INPUT_CLASS}
            />
          </Field>
          <Field label="Difficulty" htmlFor="problem-difficulty">
            <select
              id="problem-difficulty"
              value={difficulty}
              onChange={(event) => setDifficulty(event.target.value as Difficulty)}
              className={INPUT_CLASS}
            >
              <option value="easy">Easy</option>
              <option value="medium">Medium</option>
              <option value="hard">Hard</option>
            </select>
          </Field>
        </div>

        <div className="grid gap-3 lg:grid-cols-3">
          <Field label="Time limit / case" hint={`${LIMITS.minTimeSec}-${LIMITS.maxTimeSec}s`} htmlFor="problem-time">
            <input
              id="problem-time"
              type="number"
              step="0.1"
              min={LIMITS.minTimeSec}
              max={LIMITS.maxTimeSec}
              value={timeLimit}
              onChange={(event) => setTimeLimit(event.target.value)}
              className={INPUT_CLASS}
            />
          </Field>
          <Field label="Memory limit" hint="MB" htmlFor="problem-memory">
            <input
              id="problem-memory"
              type="number"
              step="16"
              min={LIMITS.minMemoryMb}
              max={LIMITS.maxMemoryMb}
              value={memoryMb}
              onChange={(event) => setMemoryMb(event.target.value)}
              className={INPUT_CLASS}
            />
          </Field>
          <Field label="Publish to class" hint="optional" htmlFor="problem-class">
            <select
              id="problem-class"
              value={classroomId}
              onChange={(event) => setClassroomId(event.target.value)}
              className={INPUT_CLASS}
            >
              <option value="">Private bank (students cannot see it)</option>
              {classrooms.map((classroom) => (
                <option key={classroom.id} value={classroom.id}>
                  {classroom.name} ({classroom.code})
                </option>
              ))}
            </select>
          </Field>
        </div>

        <div>
          <div className="mb-1 flex items-center gap-2">
            <span className="text-xs font-semibold uppercase tracking-wide text-slate-400">
              Statement
            </span>
            <span className="text-[11px] text-slate-500">Markdown supported</span>
            <button
              type="button"
              onClick={() => setPreview((previous) => !previous)}
              className="ml-auto inline-flex items-center gap-1.5 rounded-md border border-slate-700 px-2 py-1 text-[11px] text-slate-300 transition-colors hover:border-sky-500/60 hover:text-sky-300"
            >
              <Eye className="h-3 w-3" aria-hidden="true" />
              {preview ? 'Edit' : 'Preview'}
            </button>
          </div>
          {preview ? (
            <div className="max-h-64 overflow-y-auto rounded-lg border border-slate-700 bg-slate-950/60 p-3">
              <Markdown source={description} />
            </div>
          ) : (
            <textarea
              value={description}
              onChange={(event) => setDescription(event.target.value)}
              rows={8}
              spellCheck={false}
              placeholder={'# Two Sum\n\nRead two integers and print their sum.\n\n## Input\n\nTwo integers separated by a space.\n\n## Output\n\nTheir sum on a single line.'}
              className={`${INPUT_CLASS} font-mono text-[12px] leading-relaxed`}
            />
          )}
        </div>

        <div className="rounded-lg border border-slate-800 bg-slate-950/30 p-2.5">
          <button
            type="button"
            onClick={() => setShowStarter((previous) => !previous)}
            className="flex w-full items-center gap-2 text-[11px] font-semibold uppercase tracking-wider text-slate-400 transition-colors hover:text-slate-200"
          >
            <BookOpen className="h-3.5 w-3.5" aria-hidden="true" />
            Starter code (loaded into the editor when a student opens the problem)
            <span className="ml-auto normal-case tracking-normal text-slate-500">
              {showStarter ? 'Hide' : 'Show'}
            </span>
          </button>
          {showStarter ? (
            <div className="mt-2 grid gap-2 lg:grid-cols-2">
              <Field label="C" htmlFor="starter-c">
                <textarea
                  id="starter-c"
                  value={starterC}
                  onChange={(event) => setStarterC(event.target.value)}
                  spellCheck={false}
                  rows={8}
                  className={`${INPUT_CLASS} font-mono text-[11.5px]`}
                />
              </Field>
              <Field label="C++" htmlFor="starter-cpp">
                <textarea
                  id="starter-cpp"
                  value={starterCpp}
                  onChange={(event) => setStarterCpp(event.target.value)}
                  spellCheck={false}
                  rows={8}
                  className={`${INPUT_CLASS} font-mono text-[11.5px]`}
                />
              </Field>
            </div>
          ) : null}
        </div>

        <div>
          <p className="mb-1.5 flex items-center gap-2 text-xs font-semibold uppercase tracking-wide text-slate-400">
            <Layers className="h-3.5 w-3.5 text-emerald-400" aria-hidden="true" />
            Sample test cases
            <span className="text-[11px] font-normal normal-case tracking-normal text-slate-500">
              visible to students, used by "Run Samples"
            </span>
          </p>
          {renderCaseGroup(samples, true)}
        </div>

        <div>
          <p className="mb-1.5 flex items-center gap-2 text-xs font-semibold uppercase tracking-wide text-slate-400">
            <Lock className="h-3.5 w-3.5 text-rose-400" aria-hidden="true" />
            Hidden test cases
            <span className="text-[11px] font-normal normal-case tracking-normal text-slate-500">
              only verdicts are returned on submit
            </span>
          </p>
          {renderCaseGroup(hidden, false)}
        </div>

        {samples.length === 0 ? (
          <InfoNotice icon={<Info className="h-3.5 w-3.5" aria-hidden="true" />}>
            Without a sample case students get no example input/output on the problem page.
          </InfoNotice>
        ) : null}
      </div>
    </Modal>
  )
}

export default ProblemCreateModal
