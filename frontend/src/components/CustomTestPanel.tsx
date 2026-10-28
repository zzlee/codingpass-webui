/**
 * Custom Test Case Debugger Panel (Multi-Case Batch Testing).
 *
 * Allows students to manage multiple custom test cases (#1, #2, ...), import problem
 * sample cases, execute individually or in batch (one compile, multiple runs via Local Agent),
 * and optionally compare actual stdout against expected output without grading or AI overhead.
 */
import { useEffect, useMemo, useState } from 'react'
import {
  AlertTriangle,
  ArrowDownLeft,
  Check,
  CheckCircle2,
  CircleAlert,
  CircleCheck,
  CircleSlash,
  Cpu,
  Edit2,
  FolderDown,
  Gauge,
  Layers,
  Play,
  Plus,
  Stethoscope,
  Terminal,
  TimerReset,
  Trash2,
  TriangleAlert,
  X,
  XCircle,
  type LucideIcon,
} from 'lucide-react'

import { CopyButton, Spinner } from './ui'
import { buttonClass, formatMs } from '../format'
import { useLocalAgent } from '../localAgentContext'
import type { JudgeStatus, RunResponse, TestCasePublic } from '../types'

interface StatusStyle {
  label: string
  Icon: LucideIcon
  pill: string
  accent: string
  hint: string
}

const STATUS_STYLES: Record<JudgeStatus, StatusStyle> = {
  success: {
    label: 'SUCCESS',
    Icon: CircleCheck,
    pill: 'border-emerald-500/40 bg-emerald-500/15 text-emerald-300',
    accent: 'border-emerald-500/30',
    hint: 'Compiled and exited normally with status 0.',
  },
  compile_error: {
    label: 'COMPILE ERROR',
    Icon: TriangleAlert,
    pill: 'border-rose-500/40 bg-rose-500/15 text-rose-300',
    accent: 'border-rose-500/30',
    hint: 'The compiler rejected your source code. Check the compiler diagnostics below.',
  },
  time_limit_exceeded: {
    label: 'TIME LIMIT EXCEEDED',
    Icon: TimerReset,
    pill: 'border-orange-500/40 bg-orange-500/15 text-orange-300',
    accent: 'border-orange-500/30',
    hint: 'Execution exceeded the per-case time limit and was terminated.',
  },
  runtime_error: {
    label: 'RUNTIME ERROR',
    Icon: CircleAlert,
    pill: 'border-yellow-500/40 bg-yellow-500/15 text-yellow-200',
    accent: 'border-yellow-500/30',
    hint: 'The program crashed or returned a non-zero exit code.',
  },
  internal_error: {
    label: 'JUDGE ERROR',
    Icon: CircleSlash,
    pill: 'border-fuchsia-500/40 bg-fuchsia-500/15 text-fuchsia-300',
    accent: 'border-fuchsia-500/30',
    hint: 'The execution sandbox encountered an internal error. Please try again.',
  },
}

function lineCount(text: string): number {
  if (!text) return 0
  return text.replace(/\n$/, '').split('\n').length
}

function normalizeOutput(text: string): string {
  return text
    .split('\n')
    .map((line) => line.trimEnd())
    .join('\n')
    .trim()
}

function outputsMatch(actual: string, expected: string): boolean {
  return normalizeOutput(actual) === normalizeOutput(expected)
}

export interface CustomTestResult {
  response: RunResponse | null
  error: string | null
  engine: 'local' | 'server'
}

export interface CustomTestCaseItem {
  id: string
  name: string
  stdin: string
  expectedOutput: string
  result: CustomTestResult | null
}

export interface CustomTestPanelProps {
  stdin?: string
  onStdinChange?: (value: string) => void
  onRun?: () => void
  running?: boolean
  result?: CustomTestResult | null
  sampleCases: TestCasePublic[]
  localAgentConnected: boolean
  timeLimitSec?: number
  code?: string
  language?: 'c' | 'cpp'
  onBeforeRun?: (trigger: 'run_custom') => void
}

export function CustomTestPanel({
  stdin = '',
  onStdinChange,
  onRun,
  running = false,
  result = null,
  sampleCases,
  localAgentConnected,
  timeLimitSec = 2,
  code = '',
  language = 'cpp',
  onBeforeRun,
}: CustomTestPanelProps) {
  const { openModal, runLocally, runBatchLocally } = useLocalAgent()

  const [cases, setCases] = useState<CustomTestCaseItem[]>(() => [
    {
      id: 'case-1',
      name: '自訂測資 #1',
      stdin: stdin || '',
      expectedOutput: '',
      result: result || null,
    },
  ])
  const [activeCaseId, setActiveCaseId] = useState<string>('case-1')
  const [editingNameId, setEditingNameId] = useState<string | null>(null)
  const [editingNameValue, setEditingNameValue] = useState<string>('')
  const [batchRunning, setBatchRunning] = useState<boolean>(false)
  const [outputTab, setOutputTab] = useState<'stdout' | 'diff' | 'diagnostics'>('stdout')

  // Find active case
  const activeCase = useMemo(() => {
    return cases.find((c) => c.id === activeCaseId) || cases[0] || null
  }, [cases, activeCaseId])

  // Sync external single result if provided and active case doesn't have its own
  useEffect(() => {
    if (result && activeCase) {
      setCases((prev) =>
        prev.map((c) => (c.id === activeCase.id ? { ...c, result } : c)),
      )
    }
  }, [result])

  const response = activeCase?.result?.response ?? null
  const diagnostics = useMemo(() => {
    if (!response) return ''
    return [response.compile_output, response.stderr].filter(Boolean).join('\n')
  }, [response])

  const stdout = response?.stdout ?? ''
  const hasExpected = Boolean(activeCase?.expectedOutput?.trim())
  const isMatch = hasExpected && response?.status === 'success' && outputsMatch(stdout, activeCase.expectedOutput)

  // Automatically switch tab based on execution outcome
  useEffect(() => {
    if (response) {
      if (response.status === 'compile_error' || (response.status !== 'success' && !response.stdout && diagnostics)) {
        setOutputTab('diagnostics')
      } else if (hasExpected) {
        setOutputTab(isMatch ? 'stdout' : 'diff')
      } else {
        setOutputTab('stdout')
      }
    }
  }, [response, diagnostics, hasExpected, isMatch])

  // ------------------------------------------------------------- Case Actions
  const handleAddCase = () => {
    const newId = `case-${Date.now()}`
    const newCase: CustomTestCaseItem = {
      id: newId,
      name: `自訂測資 #${cases.length + 1}`,
      stdin: '',
      expectedOutput: '',
      result: null,
    }
    setCases((prev) => [...prev, newCase])
    setActiveCaseId(newId)
    onStdinChange?.('')
  }

  const handleDeleteCase = (id: string, e: React.MouseEvent) => {
    e.stopPropagation()
    if (cases.length <= 1) return
    const nextCases = cases.filter((c) => c.id !== id)
    setCases(nextCases)
    if (activeCaseId === id) {
      const nextActive = nextCases[0]?.id || ''
      setActiveCaseId(nextActive)
      const targetCase = nextCases[0]
      if (targetCase) onStdinChange?.(targetCase.stdin)
    }
  }

  const handleUpdateActiveStdin = (newStdin: string) => {
    if (!activeCase) return
    setCases((prev) =>
      prev.map((c) => (c.id === activeCase.id ? { ...c, stdin: newStdin } : c)),
    )
    onStdinChange?.(newStdin)
  }

  const handleUpdateActiveExpected = (newExpected: string) => {
    if (!activeCase) return
    setCases((prev) =>
      prev.map((c) => (c.id === activeCase.id ? { ...c, expectedOutput: newExpected } : c)),
    )
  }

  const handleImportAllSamples = () => {
    if (sampleCases.length === 0) return
    const imported: CustomTestCaseItem[] = sampleCases.map((sc, idx) => ({
      id: `sample-${idx + 1}-${Date.now()}`,
      name: `範例 #${idx + 1}`,
      stdin: sc.input,
      expectedOutput: sc.expected_output,
      result: null,
    }))
    setCases(imported)
    setActiveCaseId(imported[0].id)
    onStdinChange?.(imported[0].stdin)
  }

  const handleImportSingleSample = (sc: TestCasePublic, idx: number) => {
    if (!activeCase) return
    setCases((prev) =>
      prev.map((c) =>
        c.id === activeCase.id
          ? {
              ...c,
              name: `範例 #${idx + 1} (副本)`,
              stdin: sc.input,
              expectedOutput: sc.expected_output,
              result: null,
            }
          : c,
      ),
    )
    onStdinChange?.(sc.input)
  }

  // ---------------------------------------------------------------- Execution
  const isBusy = running || batchRunning

  const handleRunSingle = async () => {
    if (!activeCase || isBusy) return
    if (!localAgentConnected) {
      if (onRun) onRun()
      return
    }

    onBeforeRun?.('run_custom')

    if (!code) {
      if (onRun) onRun()
      return
    }

    setCases((prev) =>
      prev.map((c) =>
        c.id === activeCase.id
          ? { ...c, result: null }
          : c,
      ),
    )

    try {
      const resp = await runLocally({
        code,
        language,
        stdin: activeCase.stdin,
        timeout_sec: Math.max(1, Math.ceil(timeLimitSec)),
      })
      setCases((prev) =>
        prev.map((c) =>
          c.id === activeCase.id
            ? { ...c, result: { response: resp, error: null, engine: 'local' } }
            : c,
        ),
      )
    } catch (err) {
      setCases((prev) =>
        prev.map((c) =>
          c.id === activeCase.id
            ? {
                ...c,
                result: {
                  response: null,
                  error: err instanceof Error ? err.message : String(err),
                  engine: 'local',
                },
              }
            : c,
        ),
      )
    }
  }

  const handleRunBatch = async () => {
    if (cases.length === 0 || isBusy || !localAgentConnected) return
    onBeforeRun?.('run_custom')

    if (!code) {
      if (onRun) onRun()
      return
    }

    setBatchRunning(true)
    try {
      const stdinList = cases.map((c) => c.stdin)
      const responses = await runBatchLocally({
        code,
        language,
        stdin_list: stdinList,
        timeout_sec: Math.max(1, Math.ceil(timeLimitSec)),
      })

      setCases((prev) =>
        prev.map((c, idx) => ({
          ...c,
          result: responses[idx]
            ? { response: responses[idx], error: null, engine: 'local' }
            : c.result,
        })),
      )
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err)
      setCases((prev) =>
        prev.map((c) => ({
          ...c,
          result: { response: null, error: msg, engine: 'local' },
        })),
      )
    } finally {
      setBatchRunning(false)
    }
  }

  const status: JudgeStatus | null = response?.status ?? null
  const statusStyle = status ? STATUS_STYLES[status] : null

  return (
    <div className="flex h-full min-h-0 flex-col rounded-xl border border-slate-800 bg-slate-900/60 overflow-hidden">
      {/* Action Toolbar */}
      <div className="flex shrink-0 flex-wrap items-center gap-2 border-b border-slate-800/80 bg-slate-900/90 px-3 py-2">
        <div className="flex items-center gap-2">
          <Terminal className="h-4 w-4 text-indigo-400" aria-hidden="true" />
          <span className="text-xs font-semibold text-slate-200">自訂測資偵錯 (Custom Test Case)</span>
          {localAgentConnected ? (
            <span
              title="已連線本機 Local Agent：程式由本機 GCC/G++ 直接編譯執行，不佔用伺服器資源"
              className="inline-flex items-center gap-1 rounded-full border border-emerald-500/40 bg-emerald-500/15 px-2 py-0.5 font-mono text-[10px] font-medium text-emerald-300"
            >
              <span className="h-1.5 w-1.5 rounded-full bg-emerald-400 animate-pulse" />
              本地編譯 (Local)
            </span>
          ) : (
            <span
              title="未連線本機 Local Agent：伺服器不開放學生直接編譯，需啟動 Local Agent 進行本機編譯"
              className="inline-flex items-center gap-1 rounded-full border border-rose-500/40 bg-rose-500/15 px-2 py-0.5 font-mono text-[10px] font-medium text-rose-300"
            >
              <span className="h-1.5 w-1.5 rounded-full bg-rose-400" />
              🔴 Local Agent 未連線（需本機編譯）
            </span>
          )}

          <button
            type="button"
            onClick={() => openModal('doctor')}
            title="開啟本機編譯工具鏈診斷"
            className="inline-flex items-center gap-1 rounded border border-slate-700/80 bg-slate-800/80 px-2 py-0.5 font-mono text-[10px] text-slate-300 hover:border-indigo-500/60 hover:text-white transition-colors"
          >
            <Stethoscope className="h-3 w-3 text-indigo-400" />
            <span>工具鏈診斷</span>
          </button>
        </div>

        {/* Quick sample loader */}
        {sampleCases.length > 0 && (
          <div className="flex items-center gap-1 text-[11px] text-slate-400 sm:ml-2">
            <span className="hidden sm:inline text-slate-500">帶入範例:</span>
            {sampleCases.map((sc, idx) => (
              <button
                key={sc.id || idx}
                type="button"
                onClick={() => handleImportSingleSample(sc, idx)}
                title={`將範例 #${idx + 1} 輸入與預期輸出填入此測資`}
                className="rounded border border-slate-700/80 bg-slate-800/70 px-1.5 py-0.5 font-mono text-[10px] text-slate-300 hover:border-indigo-500/60 hover:text-white"
              >
                #{idx + 1}
              </button>
            ))}
            <button
              type="button"
              onClick={handleImportAllSamples}
              title="將所有範例測資批次匯入為自訂測資分頁"
              className="ml-1 inline-flex items-center gap-1 rounded border border-indigo-500/40 bg-indigo-500/10 px-1.5 py-0.5 text-[10px] font-medium text-indigo-300 hover:bg-indigo-500/20"
            >
              <FolderDown className="h-3 w-3" />
              <span className="hidden md:inline">匯入全部範例</span>
            </button>
          </div>
        )}

        <div className="ml-auto flex items-center gap-2">
          {activeCase?.stdin.length ? (
            <button
              type="button"
              onClick={() => handleUpdateActiveStdin('')}
              title="清空目前輸入框"
              className="inline-flex items-center gap-1 rounded px-1.5 py-1 text-[11px] text-slate-400 hover:text-rose-300"
            >
              <Trash2 className="h-3 w-3" />
              <span className="hidden sm:inline">清空</span>
            </button>
          ) : null}

          {/* Run Batch Button (Visible if multiple cases) */}
          {cases.length > 1 && (
            <button
              type="button"
              onClick={() => void handleRunBatch()}
              disabled={isBusy || !localAgentConnected}
              title="一次編譯，本機批次執行所有自訂測資 (Ctrl+Shift+Enter)"
              className={buttonClass(
                'subtle',
                `gap-1.5 text-xs ${
                  localAgentConnected
                    ? 'border-indigo-500/60 bg-indigo-500/15 text-indigo-200 hover:bg-indigo-500/25'
                    : 'bg-slate-800 border-slate-700 text-slate-500 cursor-not-allowed opacity-60'
                }`,
              )}
            >
              {batchRunning ? (
                <Spinner className="h-3.5 w-3.5" />
              ) : (
                <Layers className="h-3.5 w-3.5 text-indigo-400" />
              )}
              <span>批次執行全部 ({cases.length})</span>
            </button>
          )}

          {/* Run Single Button */}
          <button
            type="button"
            data-testid="btn-run-custom"
            onClick={() => void handleRunSingle()}
            disabled={isBusy || !localAgentConnected}
            title={
              localAgentConnected
                ? '執行此自訂測資 (Ctrl+Enter)'
                : '未連線 Local Agent：需先啟動本機代理程式才能編譯執行'
            }
            className={buttonClass(
              'primary',
              `gap-1.5 text-xs shadow-sm ${
                localAgentConnected
                  ? 'bg-indigo-600 hover:bg-indigo-500 border-indigo-500 text-white'
                  : 'bg-slate-800 border-slate-700 text-slate-500 cursor-not-allowed opacity-60'
              }`,
            )}
          >
            {isBusy && !batchRunning ? (
              <Spinner className="h-3.5 w-3.5" />
            ) : (
              <Play className="h-3.5 w-3.5 fill-current" aria-hidden="true" />
            )}
            <span>{isBusy && !batchRunning ? '執行中…' : '執行自訂測資'}</span>
            <span className="hidden sm:inline font-mono text-[10px] opacity-70">(Ctrl+Enter)</span>
          </button>
        </div>
      </div>

      {/* Disconnected Warning Banner with Doctor Button */}
      {!localAgentConnected && (
        <div className="flex flex-wrap items-center justify-between gap-2 border-b border-amber-500/20 bg-amber-500/10 px-3 py-2 text-xs text-amber-300">
          <div className="flex items-center gap-2">
            <AlertTriangle className="h-4 w-4 shrink-0 text-amber-400" />
            <span>
              伺服器不開放學生直接編譯。請啟動 CodingPass 本機編譯代理程式 (ws://127.0.0.1:8765)，透過本機 GCC/G++ 進行自訂測資測試。
            </span>
          </div>
          <button
            type="button"
            onClick={() => openModal('doctor')}
            className="inline-flex shrink-0 items-center gap-1.5 rounded border border-amber-500/40 bg-amber-500/20 px-2.5 py-1 text-xs font-semibold text-amber-200 transition-colors hover:bg-amber-500/30"
          >
            <Stethoscope className="h-3.5 w-3.5" />
            <span>開啟工具鏈診斷與安裝指引</span>
          </button>
        </div>
      )}

      {/* Multi-Case Tab Bar */}
      <div className="flex shrink-0 items-center gap-1 overflow-x-auto border-b border-slate-800/80 bg-slate-950/60 px-3 py-1.5">
        <span className="text-[11px] font-semibold text-slate-400 mr-1 flex items-center gap-1 shrink-0">
          <Layers className="h-3 w-3 text-indigo-400" />
          測資清單:
        </span>

        {cases.map((c) => {
          const isSelected = c.id === activeCase?.id
          const caseResp = c.result?.response
          const cHasExpected = Boolean(c.expectedOutput?.trim())
          const cIsMatch = cHasExpected && caseResp?.status === 'success' && outputsMatch(caseResp.stdout, c.expectedOutput)

          return (
            <div
              key={c.id}
              onClick={() => {
                setActiveCaseId(c.id)
                onStdinChange?.(c.stdin)
              }}
              className={`group flex items-center gap-1.5 rounded-lg border px-2.5 py-1 text-xs cursor-pointer transition-colors shrink-0 ${
                isSelected
                  ? 'border-indigo-500/60 bg-indigo-500/20 text-indigo-100'
                  : 'border-slate-800 bg-slate-900/60 text-slate-400 hover:border-slate-700 hover:text-slate-200'
              }`}
            >
              {/* Status Indicator Dot */}
              {caseResp ? (
                cHasExpected ? (
                  cIsMatch ? (
                    <span className="h-2 w-2 rounded-full bg-emerald-400" title="AC: 與預期輸出相符" />
                  ) : (
                    <span className="h-2 w-2 rounded-full bg-rose-400" title="WA: 輸出與預期不符" />
                  )
                ) : caseResp.status === 'success' ? (
                  <span className="h-2 w-2 rounded-full bg-emerald-400" title="成功結束" />
                ) : (
                  <span className="h-2 w-2 rounded-full bg-rose-400" title="執行錯誤或逾時" />
                )
              ) : (
                <span className="h-2 w-2 rounded-full bg-slate-600" title="尚未執行" />
              )}

              {/* Editable Name */}
              {editingNameId === c.id ? (
                <input
                  type="text"
                  autoFocus
                  value={editingNameValue}
                  onChange={(e) => setEditingNameValue(e.target.value)}
                  onBlur={() => {
                    if (editingNameValue.trim()) {
                      setCases((prev) =>
                        prev.map((item) => (item.id === c.id ? { ...item, name: editingNameValue.trim() } : item)),
                      )
                    }
                    setEditingNameId(null)
                  }}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter') {
                      if (editingNameValue.trim()) {
                        setCases((prev) =>
                          prev.map((item) => (item.id === c.id ? { ...item, name: editingNameValue.trim() } : item)),
                        )
                      }
                      setEditingNameId(null)
                    } else if (e.key === 'Escape') {
                      setEditingNameId(null)
                    }
                  }}
                  className="w-24 rounded border border-indigo-400 bg-slate-950 px-1 py-0.5 text-xs text-white outline-none"
                  onClick={(e) => e.stopPropagation()}
                />
              ) : (
                <span
                  onDoubleClick={(e) => {
                    e.stopPropagation()
                    setEditingNameId(c.id)
                    setEditingNameValue(c.name)
                  }}
                  title="連按兩下以修改名稱"
                  className="font-medium truncate max-w-[120px]"
                >
                  {c.name}
                </span>
              )}

              {/* Rename Icon button */}
              {editingNameId !== c.id && isSelected && (
                <button
                  type="button"
                  onClick={(e) => {
                    e.stopPropagation()
                    setEditingNameId(c.id)
                    setEditingNameValue(c.name)
                  }}
                  title="重新命名"
                  className="opacity-0 group-hover:opacity-100 hover:text-white"
                >
                  <Edit2 className="h-2.5 w-2.5 text-slate-400" />
                </button>
              )}

              {/* Delete button (if >1 cases) */}
              {cases.length > 1 && (
                <button
                  type="button"
                  onClick={(e) => handleDeleteCase(c.id, e)}
                  title="刪除此組測資"
                  className="rounded p-0.5 text-slate-500 hover:bg-slate-800 hover:text-rose-300"
                >
                  <X className="h-3 w-3" />
                </button>
              )}
            </div>
          )
        })}

        {/* Add Case Button */}
        <button
          type="button"
          onClick={handleAddCase}
          title="新增一組自訂測資"
          className="inline-flex items-center gap-1 rounded-lg border border-dashed border-slate-700/80 bg-slate-900/40 px-2 py-1 text-xs text-slate-400 hover:border-indigo-500/60 hover:text-indigo-300 transition-colors shrink-0"
        >
          <Plus className="h-3 w-3" />
          <span>新增測資</span>
        </button>
      </div>

      {/* Main Split Body: Left = Inputs (Stdin + Expected), Right = Output */}
      <div className="grid min-h-0 flex-1 grid-cols-1 gap-2 p-3 md:grid-cols-2 md:gap-3 overflow-y-auto">
        {/* Left: Input Column */}
        <div className="flex min-h-[14rem] flex-col gap-2 rounded-lg border border-slate-800 bg-slate-950/60 p-2.5">
          {/* Stdin Area */}
          <div className="flex flex-1 flex-col min-h-[9rem]">
            <div className="mb-1.5 flex items-center justify-between">
              <span className="flex items-center gap-1.5 font-mono text-[11px] font-semibold text-slate-300">
                <ArrowDownLeft className="h-3.5 w-3.5 text-sky-400" />
                標準輸入 (Stdin)
              </span>
              <span className="font-mono text-[10px] text-slate-500">
                {activeCase?.stdin.length.toLocaleString() ?? 0} 字元
              </span>
            </div>
            <textarea
              data-testid="custom-stdin-input"
              value={activeCase?.stdin ?? ''}
              onChange={(e) => handleUpdateActiveStdin(e.target.value)}
              onKeyDown={(e) => {
                if ((e.ctrlKey || e.metaKey) && e.shiftKey && e.key === 'Enter') {
                  e.preventDefault()
                  void handleRunBatch()
                } else if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') {
                  e.preventDefault()
                  void handleRunSingle()
                }
              }}
              placeholder={
                '輸入要傳送給程式讀取的標準輸入 (cin / scanf)...\n' +
                '例如：\n' +
                '3\n' +
                '10 20 30\n\n' +
                '提示：按 Ctrl + Enter 執行當前測資，Ctrl + Shift + Enter 批次執行全數測資。'
              }
              className="w-full flex-1 resize-none rounded-md border border-slate-800/80 bg-[#050914] p-2.5 font-mono text-[12px] leading-relaxed text-slate-100 placeholder:text-slate-600 focus:border-indigo-500/70 focus:outline-none focus:ring-1 focus:ring-indigo-500/40"
            />
          </div>

          {/* Optional Expected Output */}
          <div className="flex flex-col border-t border-slate-800/60 pt-2">
            <div className="mb-1 flex items-center justify-between">
              <span className="flex items-center gap-1 font-mono text-[11px] font-semibold text-slate-400">
                <CheckCircle2 className="h-3 w-3 text-emerald-400" />
                預期輸出 (Expected Output, 選填)
              </span>
              <span className="text-[10px] text-slate-500">填寫後自動比對結果</span>
            </div>
            <textarea
              rows={2}
              value={activeCase?.expectedOutput ?? ''}
              onChange={(e) => handleUpdateActiveExpected(e.target.value)}
              placeholder="若輸入預期輸出，執行後系統將自動比對標準輸出並標示 AC/WA..."
              className="w-full rounded-md border border-slate-800/80 bg-[#050914] p-2 font-mono text-[11px] leading-relaxed text-slate-300 placeholder:text-slate-600 focus:border-indigo-500/70 focus:outline-none"
            />
          </div>

          <p className="mt-0.5 text-[10px] text-slate-500">
            * 自訂測資僅供本地測試與邏輯校對，不會上傳伺服器儲存，亦不計入成績。
          </p>
        </div>

        {/* Right: Output Column */}
        <div className="flex min-h-[14rem] flex-col rounded-lg border border-slate-800 bg-slate-950/60 p-2.5">
          {isBusy ? (
            <div className="flex flex-1 flex-col items-center justify-center gap-2 text-slate-400">
              <Spinner className="h-6 w-6 text-indigo-400" />
              <span className="text-xs font-medium">
                {batchRunning
                  ? `本地 Local Agent 正在批次執行 ${cases.length} 組測資…`
                  : localAgentConnected
                  ? '本地 Local Agent 編譯執行中…'
                  : '伺服器沙盒編譯執行中…'}
              </span>
              <span className="text-[11px] text-slate-500">
                限時 {timeLimitSec}s · 正在等待輸出…
              </span>
            </div>
          ) : activeCase?.result?.error ? (
            <div className="flex flex-1 flex-col items-center justify-center p-4 text-center">
              <AlertTriangle className="h-8 w-8 text-rose-400 mb-2" />
              <h4 className="text-xs font-semibold text-rose-300">執行發生錯誤</h4>
              <p className="mt-1 text-[11px] text-slate-400 max-w-sm whitespace-pre-wrap font-mono">
                {activeCase.result.error}
              </p>
            </div>
          ) : response ? (
            <div className="flex flex-1 flex-col min-h-0">
              {/* Verdict header & timing */}
              <div className="mb-2 flex flex-wrap items-center justify-between gap-2 border-b border-slate-800/80 pb-2">
                <div className="flex items-center gap-2">
                  {statusStyle && (
                    <span
                      className={`inline-flex items-center gap-1 rounded-full border px-2 py-0.5 font-mono text-[10px] font-bold ${statusStyle.pill}`}
                      title={statusStyle.hint}
                    >
                      <statusStyle.Icon className="h-3 w-3" />
                      {statusStyle.label}
                    </span>
                  )}

                  {/* Expected Match Badge */}
                  {hasExpected && response.status === 'success' && (
                    isMatch ? (
                      <span className="inline-flex items-center gap-1 rounded-full border border-emerald-500/40 bg-emerald-500/20 px-2 py-0.5 font-mono text-[10px] font-bold text-emerald-300">
                        <Check className="h-3 w-3" />
                        AC 與預期相符
                      </span>
                    ) : (
                      <span className="inline-flex items-center gap-1 rounded-full border border-rose-500/40 bg-rose-500/20 px-2 py-0.5 font-mono text-[10px] font-bold text-rose-300">
                        <XCircle className="h-3 w-3" />
                        WA 與預期不符
                      </span>
                    )
                  )}

                  <span className="font-mono text-[10px] text-slate-400">
                    exit {response.exit_code ?? '-'}
                    {response.signal_name ? ` (${response.signal_name})` : ''}
                  </span>
                </div>

                <div className="flex items-center gap-2 font-mono text-[10px] text-slate-400">
                  <span className="inline-flex items-center gap-1" title="執行時間">
                    <Cpu className="h-3 w-3 text-slate-500" />
                    {formatMs(response.execution_time_ms)}
                  </span>
                  <span className="inline-flex items-center gap-1" title="編譯時間">
                    <Gauge className="h-3 w-3 text-slate-500" />
                    {formatMs(response.compile_time_ms)}
                  </span>
                  <span className="text-slate-600">|</span>
                  <span className="text-[10px] text-slate-500">
                    {activeCase?.result?.engine === 'local' ? '本地核心' : '沙盒核心'}
                  </span>
                </div>
              </div>

              {/* Tabs for stdout vs diagnostics vs diff */}
              <div className="flex items-center gap-1 mb-1.5 border-b border-slate-800/60 pb-1 text-xs">
                <button
                  type="button"
                  onClick={() => setOutputTab('stdout')}
                  className={`flex items-center gap-1 rounded px-2 py-1 font-medium transition-colors ${
                    outputTab === 'stdout'
                      ? 'bg-slate-800 text-slate-100'
                      : 'text-slate-400 hover:text-slate-200'
                  }`}
                >
                  <span>標準輸出 (Stdout)</span>
                  <span className="rounded bg-slate-900 px-1 py-0.2 font-mono text-[10px] text-slate-400">
                    {lineCount(stdout)} 行
                  </span>
                </button>

                {hasExpected && (
                  <button
                    type="button"
                    onClick={() => setOutputTab('diff')}
                    className={`flex items-center gap-1 rounded px-2 py-1 font-medium transition-colors ${
                      outputTab === 'diff'
                        ? 'bg-slate-800 text-slate-100'
                        : 'text-slate-400 hover:text-slate-200'
                    }`}
                  >
                    <span>比對預期 (Diff)</span>
                    {isMatch ? (
                      <span className="rounded bg-emerald-500/20 px-1 py-0.2 font-mono text-[10px] text-emerald-300 font-semibold">
                        MATCH
                      </span>
                    ) : (
                      <span className="rounded bg-rose-500/20 px-1 py-0.2 font-mono text-[10px] text-rose-300 font-semibold">
                        DIFF
                      </span>
                    )}
                  </button>
                )}

                <button
                  type="button"
                  onClick={() => setOutputTab('diagnostics')}
                  className={`flex items-center gap-1 rounded px-2 py-1 font-medium transition-colors ${
                    outputTab === 'diagnostics'
                      ? 'bg-slate-800 text-slate-100'
                      : 'text-slate-400 hover:text-slate-200'
                  }`}
                >
                  <span>編譯訊息 / 診斷</span>
                  {diagnostics ? (
                    <span className="rounded bg-rose-500/20 px-1 py-0.2 font-mono text-[10px] text-rose-300 font-semibold">
                      {lineCount(diagnostics)}
                    </span>
                  ) : (
                    <span className="rounded bg-slate-900 px-1 py-0.2 font-mono text-[10px] text-slate-500">
                      0
                    </span>
                  )}
                </button>

                <div className="ml-auto">
                  <CopyButton
                    value={outputTab === 'stdout' ? stdout : diagnostics}
                    label="複製"
                    className="text-[11px] py-0.5"
                  />
                </div>
              </div>

              {/* Output Content */}
              <div className="min-h-0 flex-1 overflow-auto rounded border border-slate-800/80 bg-[#050914] p-2.5">
                {outputTab === 'stdout' ? (
                  <pre className="font-mono text-[12px] leading-relaxed text-slate-100 whitespace-pre-wrap break-all">
                    {stdout ? stdout : <span className="italic text-slate-600">(程式未輸出任何內容)</span>}
                  </pre>
                ) : outputTab === 'diff' ? (
                  <div className="grid grid-cols-2 gap-2 h-full text-[11px] font-mono">
                    <div className="flex flex-col border border-slate-800/80 rounded bg-slate-950/80 p-2 overflow-auto">
                      <span className="text-slate-400 font-bold mb-1 border-b border-slate-800 pb-1">
                        實際輸出 (Actual)
                      </span>
                      <pre className="text-slate-200 whitespace-pre-wrap break-all">
                        {stdout || '(無輸出)'}
                      </pre>
                    </div>
                    <div className="flex flex-col border border-slate-800/80 rounded bg-slate-950/80 p-2 overflow-auto">
                      <span className="text-slate-400 font-bold mb-1 border-b border-slate-800 pb-1">
                        預期輸出 (Expected)
                      </span>
                      <pre className="text-emerald-300 whitespace-pre-wrap break-all">
                        {activeCase?.expectedOutput || '(無預期輸出)'}
                      </pre>
                    </div>
                  </div>
                ) : (
                  <pre className="font-mono text-[12px] leading-relaxed text-rose-200/90 whitespace-pre-wrap break-all">
                    {diagnostics ? (
                      diagnostics
                    ) : (
                      <span className="italic text-slate-600">(編譯順利通過，無錯誤或警告診斷)</span>
                    )}
                  </pre>
                )}
              </div>
            </div>
          ) : (
            <div className="flex flex-1 flex-col items-center justify-center p-4 text-center text-slate-500">
              <Terminal className="h-8 w-8 text-slate-700 mb-2" />
              <h4 className="text-xs font-semibold text-slate-400">尚未執行此組測資</h4>
              <p className="mt-1 text-[11px] text-slate-500 max-w-xs">
                在左側輸入測試資料後，點擊「執行自訂測資」或按 <kbd className="rounded border border-slate-700 px-1 font-mono text-[10px]">Ctrl+Enter</kbd>，直接透過本地編譯器測試代碼。若有多組測資，可點擊「批次執行全部」一次測試。
              </p>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

export default CustomTestPanel
