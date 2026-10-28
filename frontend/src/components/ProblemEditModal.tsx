/**
 * Visual Problem and Test Case Editor Modal (Teacher / Admin only).
 *
 * Provides comprehensive visual maintenance for problems:
 *  - Tab 1: Problem details, Markdown description with live preview, tags, limits, starter codes, and exam settings.
 *  - Tab 2: Test Case Visual Manager for adding, editing, toggling sample/hidden, and deleting test cases.
 */
import { useEffect, useMemo, useState } from 'react'
import {
  BookOpen,
  Check,
  Code,
  Eye,
  ListChecks,
  Plus,
  Save,
  Sliders,
  Tags,
  Trash2,
  X,
} from 'lucide-react'

import Markdown from './Markdown'
import { ErrorNotice, Field, Spinner } from './ui'
import { INPUT_CLASS, buttonClass } from '../format'
import api, { ApiError } from '../api'
import type { Classroom, Difficulty, ProblemDetail, TestCasePublic } from '../types'

const PRESET_TAGS = [
  '迴圈',
  '陣列',
  '指標',
  '字串',
  '函式',
  '遞迴',
  '結構體',
  '動態規劃',
  '排序搜尋',
  '數學運算',
  '貪婪演算法',
  '基礎語法',
]

export interface ProblemEditModalProps {
  open: boolean
  onClose: () => void
  problemId: number | null
  classrooms: Classroom[]
  onUpdated: (problem: ProblemDetail) => void
  onDeleted?: (problemId: number) => void
}

export default function ProblemEditModal({
  open,
  onClose,
  problemId,
  classrooms,
  onUpdated,
  onDeleted,
}: ProblemEditModalProps) {
  const [activeTab, setActiveTab] = useState<'details' | 'testcases'>('details')
  const [loading, setLoading] = useState(false)
  const [saving, setSaving] = useState(false)
  const [deleting, setDeleting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [successNotice, setSuccessNotice] = useState<string | null>(null)

  // Problem Fields
  const [title, setTitle] = useState('')
  const [difficulty, setDifficulty] = useState<Difficulty>('easy')
  const [description, setDescription] = useState('')
  const [previewDesc, setPreviewDesc] = useState(false)
  const [timeLimit, setTimeLimit] = useState('2.0')
  const [memoryMb, setMemoryMb] = useState('256')
  const [classroomId, setClassroomId] = useState<string>('')
  const [tags, setTags] = useState<string[]>([])
  const [customTagInput, setCustomTagInput] = useState('')
  const [starterC, setStarterC] = useState('')
  const [starterCpp, setStarterCpp] = useState('')
  const [showStarter, setShowStarter] = useState(false)
  const [isExam, setIsExam] = useState(false)
  const [examLockRequired, setExamLockRequired] = useState(false)
  const [maxBlurCount, setMaxBlurCount] = useState(3)

  // Test Cases State
  const [testCases, setTestCases] = useState<TestCasePublic[]>([])
  const [caseFilter, setCaseFilter] = useState<'all' | 'sample' | 'hidden'>('all')
  const [addingCase, setAddingCase] = useState(false)
  const [newCaseInput, setNewCaseInput] = useState('')
  const [newCaseOutput, setNewCaseOutput] = useState('')
  const [newCaseIsSample, setNewCaseIsSample] = useState(true)
  const [savingCaseId, setSavingCaseId] = useState<number | null>(null)

  // Load problem details and all test cases when opened
  useEffect(() => {
    if (!open || !problemId) return
    let cancelled = false
    setLoading(true)
    setError(null)
    setSuccessNotice(null)

    void (async () => {
      try {
        const [prob, cases] = await Promise.all([
          api.getProblem(problemId),
          api.listProblemTestCases(problemId),
        ])
        if (cancelled) return
        setTitle(prob.title)
        setDifficulty(prob.difficulty)
        setDescription(prob.description)
        setTimeLimit(String(prob.time_limit_sec))
        setMemoryMb(String(prob.memory_limit_mb))
        setClassroomId(prob.classroom_id ? String(prob.classroom_id) : '')
        setTags(prob.tags || [])
        setStarterC(prob.starter_code_c || '')
        setStarterCpp(prob.starter_code_cpp || '')
        setIsExam(Boolean(prob.is_exam))
        setExamLockRequired(Boolean(prob.exam_lock_required))
        setMaxBlurCount(prob.max_blur_count ?? 3)
        setTestCases(cases)
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof ApiError ? err.detail : String(err))
        }
      } finally {
        if (!cancelled) setLoading(false)
      }
    })()

    return () => {
      cancelled = true
    }
  }, [open, problemId])

  // ---------------------------------------------------------------- Tag Management
  const handleAddTag = (tag: string) => {
    const trimmed = tag.trim()
    if (!trimmed || tags.includes(trimmed)) return
    setTags([...tags, trimmed])
    setCustomTagInput('')
  }

  const handleRemoveTag = (tag: string) => {
    setTags(tags.filter((t) => t !== tag))
  }

  // ------------------------------------------------------------- Save Problem Details
  const handleSaveDetails = async () => {
    setError(null)
    setSuccessNotice(null)
    const trimmedTitle = title.trim()
    if (!trimmedTitle) {
      setError('請填寫題目名稱')
      return
    }
    const tSec = parseFloat(timeLimit)
    if (isNaN(tSec) || tSec <= 0 || tSec > 20) {
      setError('時間限制須介於 0.1 至 20 秒之間')
      return
    }
    const mMb = parseInt(memoryMb, 10)
    if (isNaN(mMb) || mMb < 16 || mMb > 2048) {
      setError('記憶體限制須介於 16 至 2048 MB 之間')
      return
    }

    setSaving(true)
    try {
      const updated = await api.updateProblem(problemId, {
        title: trimmedTitle,
        description,
        difficulty,
        time_limit_sec: tSec,
        memory_limit_mb: mMb,
        classroom_id: classroomId ? parseInt(classroomId, 10) : 0,
        tags,
        starter_code_c: starterC || null,
        starter_code_cpp: starterCpp || null,
        is_exam: isExam,
        exam_lock_required: examLockRequired,
        max_blur_count: maxBlurCount,
      })
      setSuccessNotice('題目基本資料與限制已成功儲存！')
      onUpdated(updated)
      setTimeout(() => setSuccessNotice(null), 3000)
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : String(err))
    } finally {
      setSaving(false)
    }
  }

  // -------------------------------------------------------------- Delete Problem
  const handleDeleteProblem = async () => {
    if (!window.confirm(`確定要刪除題目「${title}」嗎？\n所有相關測資與學生評測記錄皆會一併刪除，此操作無法復原。`)) {
      return
    }
    setDeleting(true)
    setError(null)
    try {
      await api.deleteProblem(problemId)
      onDeleted?.(problemId)
      onClose()
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : String(err))
      setDeleting(false)
    }
  }

  // ----------------------------------------------------------- Test Case Actions
  const handleAddNewCase = async () => {
    setError(null)
    try {
      const updatedProb = await api.addTestCase(problemId, {
        input: newCaseInput,
        expected_output: newCaseOutput,
        is_sample: newCaseIsSample,
      })
      const freshCases = await api.listProblemTestCases(problemId)
      setTestCases(freshCases)
      setAddingCase(false)
      setNewCaseInput('')
      setNewCaseOutput('')
      onUpdated(updatedProb)
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : String(err))
    }
  }

  const handleUpdateCase = async (caseItem: TestCasePublic) => {
    setSavingCaseId(caseItem.id)
    setError(null)
    try {
      const updated = await api.updateTestCase(problemId, caseItem.id, {
        input: caseItem.input,
        expected_output: caseItem.expected_output,
        is_sample: caseItem.is_sample,
      })
      setTestCases((prev) => prev.map((c) => (c.id === updated.id ? updated : c)))
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : String(err))
    } finally {
      setSavingCaseId(null)
    }
  }

  const handleDeleteCase = async (caseId: number) => {
    if (!window.confirm('確定要刪除這筆測資嗎？')) return
    setError(null)
    try {
      await api.deleteTestCase(problemId, caseId)
      setTestCases((prev) => prev.filter((c) => c.id !== caseId))
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : String(err))
    }
  }

  const filteredCases = useMemo(() => {
    if (caseFilter === 'sample') return testCases.filter((c) => c.is_sample)
    if (caseFilter === 'hidden') return testCases.filter((c) => !c.is_sample)
    return testCases
  }, [testCases, caseFilter])

  if (!open || !problemId) return null

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/80 p-4 backdrop-blur-sm">
      <div className="flex h-[92vh] max-h-[850px] w-full max-w-4xl flex-col rounded-2xl border border-slate-800 bg-slate-900 shadow-2xl shadow-slate-950 overflow-hidden">
        {/* Header */}
        <div className="flex shrink-0 items-center justify-between border-b border-slate-800 bg-slate-950/70 px-6 py-3.5">
          <div className="flex items-center gap-2.5">
            <span className="grid h-8 w-8 place-items-center rounded-lg bg-sky-500/15 border border-sky-500/30 text-sky-400">
              <BookOpen className="h-4 w-4" />
            </span>
            <div>
              <div className="flex items-center gap-2">
                <h3 className="text-sm font-semibold text-white">題目與測資在線視覺化維護</h3>
                <span className="font-mono text-xs text-slate-500">#{problemId}</span>
              </div>
              <p className="text-[11px] text-slate-400">
                出題教師與管理員可在此調整題目限制、編修題幹、單元標籤及管理隱藏測資。
              </p>
            </div>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="rounded-lg p-1.5 text-slate-400 hover:bg-slate-800 hover:text-white"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        {/* Navigation Tabs */}
        <div className="flex shrink-0 border-b border-slate-800 bg-slate-950/40 px-6 pt-2">
          <button
            type="button"
            onClick={() => setActiveTab('details')}
            className={`flex items-center gap-2 border-b-2 px-3 py-2 text-xs font-semibold transition-colors ${
              activeTab === 'details'
                ? 'border-sky-500 text-sky-400'
                : 'border-transparent text-slate-400 hover:text-slate-200'
            }`}
          >
            <Sliders className="h-3.5 w-3.5" />
            <span>題目基本資料與限制 (Details & Limits)</span>
          </button>
          <button
            type="button"
            onClick={() => setActiveTab('testcases')}
            className={`flex items-center gap-2 border-b-2 px-3 py-2 text-xs font-semibold transition-colors ${
              activeTab === 'testcases'
                ? 'border-sky-500 text-sky-400'
                : 'border-transparent text-slate-400 hover:text-slate-200'
            }`}
          >
            <ListChecks className="h-3.5 w-3.5" />
            <span>測資視覺化管理 (Test Cases: {testCases.length})</span>
          </button>
        </div>

        {/* Notices */}
        {error && (
          <div className="px-6 pt-3">
            <ErrorNotice message={error} />
          </div>
        )}
        {successNotice && (
          <div className="mx-6 mt-3 flex items-center gap-2 rounded-xl border border-emerald-500/40 bg-emerald-500/10 px-4 py-2 text-xs text-emerald-300">
            <Check className="h-4 w-4 text-emerald-400" />
            <span>{successNotice}</span>
          </div>
        )}

        {/* Modal Body */}
        <div className="min-h-0 flex-1 overflow-y-auto p-6 space-y-5">
          {loading ? (
            <div className="flex h-64 items-center justify-center gap-2 text-sm text-slate-500">
              <Spinner className="h-5 w-5 text-sky-400" />
              <span>正在載入題目資料與測資…</span>
            </div>
          ) : activeTab === 'details' ? (
            /* TAB 1: DETAILS & LIMITS */
            <div className="space-y-4">
              {/* Title & Difficulty & Classroom */}
              <div className="grid grid-cols-1 gap-4 sm:grid-cols-12">
                <div className="sm:col-span-6">
                  <Field label="題目名稱 (Title)">
                    <input
                      type="text"
                      value={title}
                      onChange={(e) => setTitle(e.target.value)}
                      placeholder="例如：Two Sum (A + B Problem)"
                      className={INPUT_CLASS}
                    />
                  </Field>
                </div>
                <div className="sm:col-span-3">
                  <Field label="難度等級 (Difficulty)">
                    <select
                      value={difficulty}
                      onChange={(e) => setDifficulty(e.target.value as Difficulty)}
                      className={INPUT_CLASS}
                    >
                      <option value="easy">Easy (初級)</option>
                      <option value="medium">Medium (中級)</option>
                      <option value="hard">Hard (進階)</option>
                    </select>
                  </Field>
                </div>
                <div className="sm:col-span-3">
                  <Field label="發布班級 (Classroom)">
                    <select
                      value={classroomId}
                      onChange={(e) => setClassroomId(e.target.value)}
                      className={INPUT_CLASS}
                    >
                      <option value="">私有題庫 (不綁定班級)</option>
                      {classrooms.map((c) => (
                        <option key={c.id} value={String(c.id)}>
                          {c.code} · {c.name}
                        </option>
                      ))}
                    </select>
                  </Field>
                </div>
              </div>

              {/* Tags Visual Manager */}
              <div className="rounded-xl border border-slate-800 bg-slate-950/40 p-3.5 space-y-2.5">
                <div className="flex items-center gap-2">
                  <Tags className="h-4 w-4 text-indigo-400" />
                  <span className="text-xs font-semibold text-slate-200">題目標籤與單元分類 (Tags)</span>
                </div>

                {/* Selected Tags */}
                <div className="flex flex-wrap items-center gap-1.5 min-h-[1.75rem]">
                  {tags.length === 0 ? (
                    <span className="text-xs text-slate-500 italic">尚無標籤，點選下方建議或輸入自訂標籤</span>
                  ) : (
                    tags.map((tag) => (
                      <span
                        key={tag}
                        className="inline-flex items-center gap-1 rounded-md border border-indigo-500/40 bg-indigo-500/15 px-2 py-0.5 text-xs text-indigo-200"
                      >
                        <span>{tag}</span>
                        <button
                          type="button"
                          onClick={() => handleRemoveTag(tag)}
                          className="hover:text-rose-300"
                        >
                          <X className="h-3 w-3" />
                        </button>
                      </span>
                    ))
                  )}
                </div>

                {/* Tag Quick Suggestions & Add Input */}
                <div className="flex flex-wrap items-center gap-1.5 pt-1 border-t border-slate-800/60">
                  <span className="text-[11px] text-slate-500 mr-1">推薦標籤:</span>
                  {PRESET_TAGS.map((pt) => {
                    const isPicked = tags.includes(pt)
                    return (
                      <button
                        key={pt}
                        type="button"
                        onClick={() => (isPicked ? handleRemoveTag(pt) : handleAddTag(pt))}
                        className={`rounded px-1.5 py-0.5 text-[11px] font-medium transition-colors ${
                          isPicked
                            ? 'bg-indigo-600/30 text-indigo-200 border border-indigo-500/50'
                            : 'bg-slate-850 text-slate-400 hover:text-slate-200 hover:bg-slate-800'
                        }`}
                      >
                        {isPicked ? '✓ ' : '+ '}
                        {pt}
                      </button>
                    )
                  })}
                </div>

                <div className="flex items-center gap-2 pt-1">
                  <input
                    type="text"
                    value={customTagInput}
                    onChange={(e) => setCustomTagInput(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === 'Enter') {
                        e.preventDefault()
                        handleAddTag(customTagInput)
                      }
                    }}
                    placeholder="輸入自訂標籤名稱後按 Enter..."
                    className="max-w-xs rounded-lg border border-slate-700 bg-[#050914] px-2.5 py-1 text-xs text-slate-200 focus:border-indigo-500 outline-none"
                  />
                  <button
                    type="button"
                    onClick={() => handleAddTag(customTagInput)}
                    className="rounded border border-slate-700 bg-slate-800 px-2 py-1 text-xs text-slate-300 hover:text-white"
                  >
                    新增標籤
                  </button>
                </div>
              </div>

              {/* Execution Limits */}
              <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
                <Field
                  label="時間限制 (秒 / 每測資)"
                  hint="0.1 ~ 20.0 秒。建議 1.0 ~ 3.0s"
                >
                  <input
                    type="number"
                    step="0.5"
                    min="0.1"
                    max="20"
                    value={timeLimit}
                    onChange={(e) => setTimeLimit(e.target.value)}
                    className={INPUT_CLASS}
                  />
                </Field>
                <Field
                  label="記憶體限制 (MB / 每測資)"
                  hint="16 ~ 2048 MB。預設 256MB"
                >
                  <input
                    type="number"
                    step="16"
                    min="16"
                    max="2048"
                    value={memoryMb}
                    onChange={(e) => setMemoryMb(e.target.value)}
                    className={INPUT_CLASS}
                  />
                </Field>
              </div>

              {/* Exam Lockdown Settings */}
              <div className="rounded-xl border border-slate-800 bg-slate-950/40 p-3.5 space-y-2">
                <label className="flex items-center gap-2 text-xs font-semibold text-slate-200 cursor-pointer">
                  <input
                    type="checkbox"
                    checked={isExam}
                    onChange={(e) => setIsExam(e.target.checked)}
                    className="rounded border-slate-700 bg-slate-800 text-sky-500"
                  />
                  <span>啟動考試模式 (Exam Lockdown Mode)</span>
                </label>
                {isExam && (
                  <div className="ml-5 space-y-2 pt-1 border-t border-slate-800/60 text-xs text-slate-300">
                    <label className="flex items-center gap-2 cursor-pointer">
                      <input
                        type="checkbox"
                        checked={examLockRequired}
                        onChange={(e) => setExamLockRequired(e.target.checked)}
                        className="rounded border-slate-700 bg-slate-800 text-sky-500"
                      />
                      <span>嚴格要求 Windows 本地代理 Kiosk 鎖定 (禁止雙螢幕與切換)</span>
                    </label>
                    <div className="flex items-center gap-2">
                      <span>切換視窗違規容許次數 (Max Blurs):</span>
                      <input
                        type="number"
                        min="0"
                        max="20"
                        value={maxBlurCount}
                        onChange={(e) => setMaxBlurCount(parseInt(e.target.value, 10) || 0)}
                        className="w-20 rounded border border-slate-700 bg-slate-900 px-2 py-0.5 text-xs text-white"
                      />
                    </div>
                  </div>
                )}
              </div>

              {/* Markdown Description */}
              <div className="space-y-1.5">
                <div className="flex items-center justify-between">
                  <span className="text-xs font-semibold text-slate-200">題幹說明 (Markdown 格式)</span>
                  <button
                    type="button"
                    onClick={() => setPreviewDesc(!previewDesc)}
                    className="inline-flex items-center gap-1 rounded px-2 py-0.5 text-xs text-slate-400 hover:text-white"
                  >
                    <Eye className="h-3 w-3" />
                    <span>{previewDesc ? '切換編輯模式' : '即時預覽 (Preview)'}</span>
                  </button>
                </div>
                {previewDesc ? (
                  <div className="min-h-[12rem] rounded-xl border border-slate-800 bg-[#050914] p-4">
                    <Markdown source={description || '*(無題幹說明)*'} />
                  </div>
                ) : (
                  <textarea
                    rows={8}
                    value={description}
                    onChange={(e) => setDescription(e.target.value)}
                    placeholder="# 題目敘述\n\n請輸入兩整數 a 與 b，輸出其總和。\n\n### 輸入格式\n兩整數以空白分隔。\n\n### 輸出格式\n印出一行兩數相加之結果。"
                    className="w-full rounded-xl border border-slate-800 bg-[#050914] p-3 font-mono text-xs leading-relaxed text-slate-200 placeholder:text-slate-600 focus:border-sky-500 focus:outline-none"
                  />
                )}
              </div>

              {/* Starter Code Accordion */}
              <div className="rounded-xl border border-slate-800 bg-slate-950/40 p-3">
                <button
                  type="button"
                  onClick={() => setShowStarter(!showStarter)}
                  className="flex w-full items-center justify-between text-xs font-semibold text-slate-300"
                >
                  <span className="flex items-center gap-1.5">
                    <Code className="h-4 w-4 text-indigo-400" />
                    <span>預設範本程式碼 (Starter Code: C / C++)</span>
                  </span>
                  <span className="text-[11px] text-slate-500">{showStarter ? '收合 ▲' : '展開編輯 ▼'}</span>
                </button>
                {showStarter && (
                  <div className="mt-3 grid grid-cols-1 gap-3 md:grid-cols-2 pt-2 border-t border-slate-800/60">
                    <div>
                      <span className="text-[11px] font-mono text-slate-400 block mb-1">C 範本代碼:</span>
                      <textarea
                        rows={6}
                        value={starterC}
                        onChange={(e) => setStarterC(e.target.value)}
                        className="w-full rounded-lg border border-slate-800 bg-[#050914] p-2 font-mono text-xs text-slate-200"
                      />
                    </div>
                    <div>
                      <span className="text-[11px] font-mono text-slate-400 block mb-1">C++ 範本代碼:</span>
                      <textarea
                        rows={6}
                        value={starterCpp}
                        onChange={(e) => setStarterCpp(e.target.value)}
                        className="w-full rounded-lg border border-slate-800 bg-[#050914] p-2 font-mono text-xs text-slate-200"
                      />
                    </div>
                  </div>
                )}
              </div>
            </div>
          ) : (
            /* TAB 2: TEST CASES VISUAL MANAGER */
            <div className="space-y-4">
              {/* Toolbar */}
              <div className="flex flex-wrap items-center justify-between gap-2 border-b border-slate-800 pb-3">
                <div className="flex items-center gap-1.5 text-xs">
                  <span className="text-slate-400 font-semibold mr-1">篩選顯示:</span>
                  <button
                    type="button"
                    onClick={() => setCaseFilter('all')}
                    className={`rounded px-2 py-1 font-medium transition-colors ${
                      caseFilter === 'all' ? 'bg-sky-500/20 text-sky-300' : 'text-slate-400 hover:text-white'
                    }`}
                  >
                    全部 ({testCases.length})
                  </button>
                  <button
                    type="button"
                    onClick={() => setCaseFilter('sample')}
                    className={`rounded px-2 py-1 font-medium transition-colors ${
                      caseFilter === 'sample' ? 'bg-emerald-500/20 text-emerald-300' : 'text-slate-400 hover:text-white'
                    }`}
                  >
                    公開範例 ({testCases.filter((c) => c.is_sample).length})
                  </button>
                  <button
                    type="button"
                    onClick={() => setCaseFilter('hidden')}
                    className={`rounded px-2 py-1 font-medium transition-colors ${
                      caseFilter === 'hidden' ? 'bg-amber-500/20 text-amber-300' : 'text-slate-400 hover:text-white'
                    }`}
                  >
                    隱藏測資 ({testCases.filter((c) => !c.is_sample).length})
                  </button>
                </div>

                <button
                  type="button"
                  onClick={() => setAddingCase(true)}
                  className={buttonClass('primary', 'text-xs gap-1.5')}
                >
                  <Plus className="h-3.5 w-3.5" />
                  <span>新增一筆測資</span>
                </button>
              </div>

              {/* Add New Case Inline Box */}
              {addingCase && (
                <div className="rounded-xl border border-sky-500/40 bg-sky-500/5 p-4 space-y-3">
                  <div className="flex items-center justify-between">
                    <span className="text-xs font-semibold text-sky-200">新增測資項目</span>
                    <label className="flex items-center gap-1.5 text-xs text-slate-300 cursor-pointer">
                      <input
                        type="checkbox"
                        checked={newCaseIsSample}
                        onChange={(e) => setNewCaseIsSample(e.target.checked)}
                        className="rounded border-slate-700 bg-slate-900 text-sky-500"
                      />
                      <span>設為公開範例 (學生可見)</span>
                    </label>
                  </div>
                  <div className="grid grid-cols-1 md:grid-cols-2 gap-3 font-mono text-xs">
                    <div>
                      <span className="text-[11px] text-slate-400 mb-1 block">輸入資料 (Standard Input):</span>
                      <textarea
                        rows={3}
                        value={newCaseInput}
                        onChange={(e) => setNewCaseInput(e.target.value)}
                        placeholder="請輸入標準輸入內容..."
                        className="w-full rounded-lg border border-slate-700 bg-slate-950 p-2 text-slate-100"
                      />
                    </div>
                    <div>
                      <span className="text-[11px] text-slate-400 mb-1 block">預期輸出 (Expected Output):</span>
                      <textarea
                        rows={3}
                        value={newCaseOutput}
                        onChange={(e) => setNewCaseOutput(e.target.value)}
                        placeholder="請輸入預期標準輸出內容..."
                        className="w-full rounded-lg border border-slate-700 bg-slate-950 p-2 text-emerald-200"
                      />
                    </div>
                  </div>
                  <div className="flex items-center justify-end gap-2">
                    <button
                      type="button"
                      onClick={() => setAddingCase(false)}
                      className={buttonClass('ghost', 'text-xs')}
                    >
                      取消
                    </button>
                    <button
                      type="button"
                      onClick={handleAddNewCase}
                      className={buttonClass('primary', 'text-xs')}
                    >
                      確認新增
                    </button>
                  </div>
                </div>
              )}

              {/* Test Cases List */}
              {filteredCases.length === 0 ? (
                <div className="py-12 text-center text-xs text-slate-500">
                  查無符合條件之測資項目
                </div>
              ) : (
                <div className="space-y-3">
                  {filteredCases.map((caseItem, idx) => (
                    <div
                      key={caseItem.id}
                      className="rounded-xl border border-slate-800 bg-slate-950/60 p-3.5 space-y-2.5 transition-colors hover:border-slate-700"
                    >
                      <div className="flex items-center justify-between">
                        <div className="flex items-center gap-2">
                          <span className="font-mono text-xs font-bold text-slate-300">
                            #{idx + 1}
                          </span>
                          <span className="font-mono text-[10px] text-slate-500">
                            (ID: {caseItem.id})
                          </span>
                          <label className="flex items-center gap-1.5 text-[11px] cursor-pointer ml-2">
                            <input
                              type="checkbox"
                              checked={caseItem.is_sample}
                              onChange={(e) => {
                                const updated = { ...caseItem, is_sample: e.target.checked }
                                setTestCases((prev) => prev.map((c) => (c.id === caseItem.id ? updated : c)))
                              }}
                              className="rounded border-slate-700 bg-slate-900 text-sky-500"
                            />
                            <span
                              className={`rounded px-1.5 py-0.2 font-semibold text-[10px] ${
                                caseItem.is_sample
                                  ? 'bg-emerald-500/20 text-emerald-300'
                                  : 'bg-amber-500/20 text-amber-300'
                              }`}
                            >
                              {caseItem.is_sample ? '公開範例 (Sample)' : '隱藏測資 (Hidden)'}
                            </span>
                          </label>
                        </div>

                        <div className="flex items-center gap-2">
                          <button
                            type="button"
                            onClick={() => handleUpdateCase(caseItem)}
                            disabled={savingCaseId === caseItem.id}
                            className="inline-flex items-center gap-1 rounded bg-slate-800 px-2 py-1 text-[11px] text-slate-300 hover:text-white"
                          >
                            {savingCaseId === caseItem.id ? <Spinner className="h-3 w-3" /> : <Save className="h-3 w-3" />}
                            <span>儲存變更</span>
                          </button>
                          <button
                            type="button"
                            onClick={() => handleDeleteCase(caseItem.id)}
                            className="rounded p-1 text-slate-500 hover:bg-slate-800 hover:text-rose-300"
                            title="刪除此測資"
                          >
                            <Trash2 className="h-3.5 w-3.5" />
                          </button>
                        </div>
                      </div>

                      {/* Inputs & Outputs Grid */}
                      <div className="grid grid-cols-1 md:grid-cols-2 gap-2.5 font-mono text-xs">
                        <div className="space-y-1">
                          <span className="text-[10px] uppercase font-bold text-slate-500">
                            輸入內容 (Input)
                          </span>
                          <textarea
                            rows={3}
                            value={caseItem.input}
                            onChange={(e) => {
                              const val = e.target.value
                              setTestCases((prev) =>
                                prev.map((c) => (c.id === caseItem.id ? { ...c, input: val } : c)),
                              )
                            }}
                            className="w-full rounded-md border border-slate-800 bg-[#050914] p-2 text-slate-200 focus:border-indigo-500"
                          />
                        </div>

                        <div className="space-y-1">
                          <span className="text-[10px] uppercase font-bold text-slate-500">
                            預期輸出 (Expected Output)
                          </span>
                          <textarea
                            rows={3}
                            value={caseItem.expected_output}
                            onChange={(e) => {
                              const val = e.target.value
                              setTestCases((prev) =>
                                prev.map((c) => (c.id === caseItem.id ? { ...c, expected_output: val } : c)),
                              )
                            }}
                            className="w-full rounded-md border border-slate-800 bg-[#050914] p-2 text-emerald-200 focus:border-indigo-500"
                          />
                        </div>
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}
        </div>

        {/* Footer */}
        <div className="flex shrink-0 items-center justify-between border-t border-slate-800 bg-slate-950/80 px-6 py-3">
          <button
            type="button"
            onClick={handleDeleteProblem}
            disabled={deleting || saving}
            className="inline-flex items-center gap-1 rounded-lg border border-rose-500/30 bg-rose-500/10 px-3 py-1.5 text-xs font-semibold text-rose-300 hover:bg-rose-500/20"
          >
            {deleting ? <Spinner className="h-3.5 w-3.5" /> : <Trash2 className="h-3.5 w-3.5" />}
            <span>刪除此題目</span>
          </button>

          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={onClose}
              className={buttonClass('ghost', 'text-xs')}
            >
              關閉
            </button>
            {activeTab === 'details' && (
              <button
                type="button"
                onClick={handleSaveDetails}
                disabled={saving || loading}
                className={buttonClass('primary', 'text-xs gap-1.5 shadow-md')}
              >
                {saving ? <Spinner className="h-3.5 w-3.5" /> : <Save className="h-3.5 w-3.5" />}
                <span>儲存題目資料與限制</span>
              </button>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}
