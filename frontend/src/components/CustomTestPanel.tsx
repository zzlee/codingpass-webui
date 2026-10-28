/**
 * Custom Test Case Debugger Panel.
 *
 * Allows students to enter arbitrary standard input (stdin) and run their code
 * locally (via Local Agent) or via the server scratchpad, without recording any
 * submissions, without grading against hidden test cases, and without AI review.
 */
import { useEffect, useMemo, useState } from 'react'
import {
  AlertTriangle,
  ArrowDownLeft,
  CircleAlert,
  CircleCheck,
  CircleSlash,
  Cpu,
  Gauge,
  Play,
  Terminal,
  TimerReset,
  Trash2,
  TriangleAlert,
  type LucideIcon,
} from 'lucide-react'

import { CopyButton, Spinner } from './ui'
import { buttonClass, formatMs } from '../format'
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

export interface CustomTestResult {
  response: RunResponse | null
  error: string | null
  engine: 'local' | 'server'
}

export interface CustomTestPanelProps {
  stdin: string
  onStdinChange: (value: string) => void
  onRun: () => void
  running: boolean
  result: CustomTestResult | null
  sampleCases: TestCasePublic[]
  localAgentConnected: boolean
  timeLimitSec?: number
}

export function CustomTestPanel({
  stdin,
  onStdinChange,
  onRun,
  running,
  result,
  sampleCases,
  localAgentConnected,
  timeLimitSec = 2,
}: CustomTestPanelProps) {
  const [outputTab, setOutputTab] = useState<'stdout' | 'diagnostics'>('stdout')

  const response = result?.response
  const diagnostics = useMemo(() => {
    if (!response) return ''
    return [response.compile_output, response.stderr].filter(Boolean).join('\n')
  }, [response])

  // Automatically switch to diagnostics if compilation failed or if there are errors and no stdout
  useEffect(() => {
    if (response) {
      if (response.status === 'compile_error' || (response.status !== 'success' && !response.stdout && diagnostics)) {
        setOutputTab('diagnostics')
      } else {
        setOutputTab('stdout')
      }
    }
  }, [response, diagnostics])

  const status: JudgeStatus | null = response?.status ?? null
  const statusStyle = status ? STATUS_STYLES[status] : null
  const stdout = response?.stdout ?? ''

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
              title="未連線本機 Local Agent：由伺服器沙盒暫存執行，不上傳成績"
              className="inline-flex items-center gap-1 rounded-full border border-slate-700 bg-slate-800 px-2 py-0.5 font-mono text-[10px] text-slate-400"
            >
              ☁️ 伺服器暫存執行
            </span>
          )}
        </div>

        {/* Quick sample loader */}
        {sampleCases.length > 0 && (
          <div className="flex items-center gap-1 text-[11px] text-slate-400 sm:ml-2">
            <span className="hidden sm:inline text-slate-500">帶入範例:</span>
            {sampleCases.map((sc, idx) => (
              <button
                key={sc.id || idx}
                type="button"
                onClick={() => onStdinChange(sc.input)}
                title={`將範例 #${idx + 1} 輸入填入自訂測資框`}
                className="rounded border border-slate-700/80 bg-slate-800/70 px-1.5 py-0.5 font-mono text-[10px] text-slate-300 hover:border-indigo-500/60 hover:text-white"
              >
                #{idx + 1}
              </button>
            ))}
          </div>
        )}

        <div className="ml-auto flex items-center gap-2">
          {stdin.length > 0 && (
            <button
              type="button"
              onClick={() => onStdinChange('')}
              title="清空輸入框"
              className="inline-flex items-center gap-1 rounded px-1.5 py-1 text-[11px] text-slate-400 hover:text-rose-300"
            >
              <Trash2 className="h-3 w-3" />
              <span className="hidden sm:inline">清空</span>
            </button>
          )}

          <button
            type="button"
            data-testid="btn-run-custom"
            onClick={onRun}
            disabled={running}
            className={buttonClass(
              'primary',
              'gap-1.5 text-xs bg-indigo-600 hover:bg-indigo-500 border-indigo-500 text-white shadow-sm',
            )}
          >
            {running ? (
              <Spinner className="h-3.5 w-3.5" />
            ) : (
              <Play className="h-3.5 w-3.5 fill-current" aria-hidden="true" />
            )}
            <span>{running ? '執行中…' : '執行自訂測資'}</span>
            <span className="hidden sm:inline font-mono text-[10px] opacity-70">(Ctrl+Enter)</span>
          </button>
        </div>
      </div>

      {/* Main Split Body: Left = Stdin, Right = Output */}
      <div className="grid min-h-0 flex-1 grid-cols-1 gap-2 p-3 md:grid-cols-2 md:gap-3 overflow-y-auto">
        {/* Left: Stdin Column */}
        <div className="flex min-h-[10rem] flex-col rounded-lg border border-slate-800 bg-slate-950/60 p-2.5">
          <div className="mb-1.5 flex items-center justify-between">
            <span className="flex items-center gap-1.5 font-mono text-[11px] font-semibold text-slate-300">
              <ArrowDownLeft className="h-3.5 w-3.5 text-sky-400" />
              標準輸入 (Stdin)
            </span>
            <span className="font-mono text-[10px] text-slate-500">
              {stdin.length.toLocaleString()} 字元
            </span>
          </div>
          <textarea
            data-testid="custom-stdin-input"
            value={stdin}
            onChange={(e) => onStdinChange(e.target.value)}
            onKeyDown={(e) => {
              if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') {
                e.preventDefault()
                onRun()
              }
            }}
            placeholder={
              '輸入要傳送給程式讀取的標準輸入 (cin / scanf)...\n' +
              '例如：\n' +
              '3\n' +
              '10 20 30\n\n' +
              '提示：在此輸入框中按 Ctrl + Enter 亦可直接執行。'
            }
            className="w-full flex-1 resize-none rounded-md border border-slate-800/80 bg-[#050914] p-2.5 font-mono text-[12px] leading-relaxed text-slate-100 placeholder:text-slate-600 focus:border-indigo-500/70 focus:outline-none focus:ring-1 focus:ring-indigo-500/40"
          />
          <p className="mt-1.5 text-[10px] text-slate-500">
            * 自訂測資僅供本地測試與邏輯校對，不會上傳伺服器儲存，亦不計入成績。
          </p>
        </div>

        {/* Right: Output Column */}
        <div className="flex min-h-[10rem] flex-col rounded-lg border border-slate-800 bg-slate-950/60 p-2.5">
          {running ? (
            <div className="flex flex-1 flex-col items-center justify-center gap-2 text-slate-400">
              <Spinner className="h-6 w-6 text-indigo-400" />
              <span className="text-xs font-medium">
                {localAgentConnected ? '本地 Local Agent 編譯執行中…' : '伺服器沙盒編譯執行中…'}
              </span>
              <span className="text-[11px] text-slate-500">
                限時 {timeLimitSec}s · 正在等待輸出…
              </span>
            </div>
          ) : result?.error ? (
            <div className="flex flex-1 flex-col items-center justify-center p-4 text-center">
              <AlertTriangle className="h-8 w-8 text-rose-400 mb-2" />
              <h4 className="text-xs font-semibold text-rose-300">執行發生錯誤</h4>
              <p className="mt-1 text-[11px] text-slate-400 max-w-sm whitespace-pre-wrap font-mono">
                {result.error}
              </p>
            </div>
          ) : response ? (
            <div className="flex flex-1 flex-col min-h-0">
              {/* Verdict header & timing */}
              <div className="mb-2 flex flex-wrap items-center justify-between gap-2 border-b border-slate-800/80 pb-2">
                <div className="flex items-center gap-1.5">
                  {statusStyle && (
                    <span
                      className={`inline-flex items-center gap-1 rounded-full border px-2 py-0.5 font-mono text-[10px] font-bold ${statusStyle.pill}`}
                      title={statusStyle.hint}
                    >
                      <statusStyle.Icon className="h-3 w-3" />
                      {statusStyle.label}
                    </span>
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
                    {result?.engine === 'local' ? '本地核心' : '沙盒核心'}
                  </span>
                </div>
              </div>

              {/* Tabs for stdout vs diagnostics */}
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
              <h4 className="text-xs font-semibold text-slate-400">尚未執行自訂測資</h4>
              <p className="mt-1 text-[11px] text-slate-500 max-w-xs">
                在左側輸入測試資料後，點擊「執行自訂測資」或按 <kbd className="rounded border border-slate-700 px-1 font-mono text-[10px]">Ctrl+Enter</kbd>，直接透過本地/沙盒編譯器測試代碼。
              </p>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

export default CustomTestPanel
