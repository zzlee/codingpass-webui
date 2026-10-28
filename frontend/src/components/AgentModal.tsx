import { useEffect, useState } from 'react'
import {
  AlertTriangle,
  CheckCircle2,
  CircleAlert,
  CircleCheck,
  CircleX,
  Cpu,
  Download,
  FolderCheck,
  FolderX,
  Laptop,
  RefreshCw,
  ShieldCheck,
  Stethoscope,
  Terminal,
  WifiOff,
  Wrench,
  X,
  Zap,
} from 'lucide-react'

import { useLocalAgent } from '../localAgentContext'
import { buttonClass } from '../format'
import { CopyButton, Spinner } from './ui'
import type { DoctorReport } from '../types'

export interface AgentModalProps {
  open: boolean
  onClose: () => void
  initialTab?: 'setup' | 'doctor'
}

export default function AgentModal({ open, onClose, initialTab = 'setup' }: AgentModalProps) {
  const { status, compilerInfo, agentVersion, checkConnection, doctor } = useLocalAgent()
  const [activeTab, setActiveTab] = useState<'setup' | 'doctor'>(initialTab)
  const [downloading, setDownloading] = useState(false)
  const [doctorReport, setDoctorReport] = useState<DoctorReport | null>(null)
  const [runningDoctor, setRunningDoctor] = useState(false)
  const [doctorError, setDoctorError] = useState<string | null>(null)

  useEffect(() => {
    if (open) {
      setActiveTab(initialTab)
    }
  }, [open, initialTab])

  // Automatically probe doctor when switching to doctor tab if connected
  useEffect(() => {
    if (open && activeTab === 'doctor' && status === 'connected' && !doctorReport && !runningDoctor) {
      void handleRunDoctor()
    }
  }, [open, activeTab, status])

  if (!open) return null

  const handleRunDoctor = async () => {
    setRunningDoctor(true)
    setDoctorError(null)
    try {
      const rep = await doctor()
      setDoctorReport(rep)
    } catch (err) {
      setDoctorError(err instanceof Error ? err.message : String(err))
    } finally {
      setRunningDoctor(false)
    }
  }

  const handleDownload = () => {
    setDownloading(true)
    const link = document.createElement('a')
    link.href = '/api/agent/download'
    link.download = 'codingpass-agent.zip'
    document.body.appendChild(link)
    link.click()
    document.body.removeChild(link)
    setTimeout(() => setDownloading(false), 2000)
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/80 p-4 backdrop-blur-sm">
      <div className="relative flex max-h-[92vh] w-full max-w-2xl flex-col overflow-hidden rounded-2xl border border-slate-800 bg-slate-900 shadow-2xl shadow-black/60">
        {/* Header */}
        <div className="flex shrink-0 items-center justify-between border-b border-slate-800 bg-slate-950/60 px-5 py-4">
          <div className="flex items-center gap-2.5">
            <span className="grid h-9 w-9 place-items-center rounded-xl bg-gradient-to-br from-emerald-500 to-sky-500 shadow-md shadow-sky-950/50">
              <Laptop className="h-5 w-5 text-white" strokeWidth={2.2} />
            </span>
            <div>
              <h2 className="text-base font-bold text-white">CodingPass 本地加速代理 (Local Agent)</h2>
              <p className="text-xs text-slate-400">
                將日常編譯搬移到學生電腦，零伺服器延遲與負載
              </p>
            </div>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="rounded-lg p-1.5 text-slate-400 transition-colors hover:bg-slate-800 hover:text-white"
          >
            <X className="h-5 w-5" />
          </button>
        </div>

        {/* Tab Switcher */}
        <div className="flex shrink-0 border-b border-slate-800 bg-slate-950/40 px-5 pt-2">
          <button
            type="button"
            onClick={() => setActiveTab('setup')}
            className={`flex items-center gap-2 border-b-2 px-3 py-2 text-xs font-semibold transition-colors ${
              activeTab === 'setup'
                ? 'border-sky-500 text-sky-400'
                : 'border-transparent text-slate-400 hover:text-slate-200'
            }`}
          >
            <Download className="h-3.5 w-3.5" />
            <span>下載與連線設定 (Setup)</span>
          </button>
          <button
            type="button"
            onClick={() => setActiveTab('doctor')}
            className={`flex items-center gap-2 border-b-2 px-3 py-2 text-xs font-semibold transition-colors ${
              activeTab === 'doctor'
                ? 'border-emerald-500 text-emerald-400'
                : 'border-transparent text-slate-400 hover:text-slate-200'
            }`}
          >
            <Stethoscope className="h-3.5 w-3.5" />
            <span>本機工具鏈一鍵診斷 (Doctor)</span>
            {doctorReport && (
              <span
                className={`ml-1 h-2 w-2 rounded-full ${
                  doctorReport.status === 'ok' ? 'bg-emerald-400' : 'bg-amber-400'
                }`}
              />
            )}
          </button>
        </div>

        {/* Body */}
        <div className="flex-1 space-y-5 overflow-y-auto p-5 text-sm text-slate-300">
          {/* Status Banner */}
          <div
            className={`flex items-start gap-3 rounded-xl border p-4 ${
              status === 'connected'
                ? 'border-emerald-500/40 bg-emerald-500/10 text-emerald-200'
                : status === 'connecting'
                ? 'border-sky-500/30 bg-sky-500/10 text-sky-200'
                : 'border-slate-700 bg-slate-800/40 text-slate-300'
            }`}
          >
            {status === 'connected' ? (
              <CheckCircle2 className="mt-0.5 h-5 w-5 shrink-0 text-emerald-400" />
            ) : status === 'connecting' ? (
              <RefreshCw className="mt-0.5 h-5 w-5 shrink-0 animate-spin text-sky-400" />
            ) : (
              <WifiOff className="mt-0.5 h-5 w-5 shrink-0 text-slate-400" />
            )}
            <div className="min-w-0 flex-1">
              <div className="flex items-center justify-between gap-2">
                <span className="font-semibold text-white">
                  {status === 'connected'
                    ? '🟢 本地代理運行中 (已連線)'
                    : status === 'connecting'
                    ? '連線本地代理中 (ws://127.0.0.1:8765)...'
                    : '🔴 未連線到本地代理 (伺服器端不開放直接編譯)'}
                </span>
                <button
                  type="button"
                  onClick={checkConnection}
                  title="立即重新嘗試連線"
                  className="inline-flex items-center gap-1 rounded bg-slate-800/80 px-2 py-0.5 text-xs text-slate-300 hover:bg-slate-700"
                >
                  <RefreshCw className="h-3 w-3" />
                  <span>重新偵測</span>
                </button>
              </div>

              {status === 'connected' ? (
                <div className="mt-2 space-y-1 font-mono text-xs text-emerald-300/90">
                  <p>編譯器：{compilerInfo || 'GCC / Clang'}</p>
                  <p>代理版本：v{agentVersion || '1.0.0'}</p>
                  <p className="font-sans text-[11px] text-emerald-400/80">
                    ✨ 題目範例測資與自訂測資皆由本機即時編譯執行，免排隊且不上傳代碼！
                  </p>
                </div>
              ) : (
                <p className="mt-1 text-xs text-slate-400">
                  下載並在背景執行代理程式後，網頁將自動偵測並點亮綠燈。
                </p>
              )}
            </div>
          </div>

          {activeTab === 'setup' ? (
            <>
              {/* Action Download */}
              <div className="flex flex-col gap-3 rounded-xl border border-slate-800 bg-slate-950/40 p-4">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <div>
                    <h3 className="font-semibold text-white">取得學生本地端安裝包</h3>
                    <p className="text-xs text-slate-400">
                      包含 Python 輕量服務與跨平台啟動腳本 (約 10 KB，零外部第三方套件依賴)
                    </p>
                  </div>
                  <button
                    type="button"
                    onClick={handleDownload}
                    disabled={downloading}
                    className={buttonClass('primary', 'gap-1.5 px-3 py-1.5 text-xs shadow-md')}
                  >
                    <Download className="h-4 w-4" />
                    <span>{downloading ? '下載中…' : '下載 codingpass-agent.zip'}</span>
                  </button>
                </div>
              </div>

              {/* Setup Guide */}
              <div className="space-y-3">
                <h3 className="flex items-center gap-2 font-semibold text-white">
                  <Terminal className="h-4 w-4 text-sky-400" />
                  <span>快速使用說明 (只需 3 步驟)</span>
                </h3>
                <ol className="list-inside list-decimal space-y-2 rounded-xl border border-slate-800 bg-slate-900/60 p-4 text-xs text-slate-300">
                  <li className="leading-relaxed">
                    <span className="font-medium text-slate-200">下載並解壓縮：</span> 將{' '}
                    <code className="rounded bg-slate-800 px-1 py-0.5 text-sky-300">codingpass-agent.zip</code>{' '}
                    解壓縮至電腦任何資料夾。
                  </li>
                  <li className="leading-relaxed">
                    <span className="font-medium text-slate-200">執行啟動腳本：</span>
                    <div className="mt-2 space-y-1 pl-4">
                      <div className="rounded bg-slate-950 p-2 font-mono text-[11px] text-slate-200">
                        <span className="text-slate-500"># Windows (雙擊或終端機執行):</span>
                        <br />
                        run_agent.bat
                        <br />
                        <span className="text-slate-500"># macOS / Linux (終端機執行):</span>
                        <br />
                        chmod +x run_agent.sh && ./run_agent.sh
                      </div>
                    </div>
                  </li>
                  <li className="leading-relaxed">
                    <span className="font-medium text-slate-200">保持背景執行：</span> 終端機顯示{' '}
                    <code className="rounded bg-slate-800 px-1 py-0.5 text-emerald-400">
                      CodingPass Local Agent started on ws://127.0.0.1:8765
                    </code>
                    ，此網頁頂部會立即轉為「本地編譯已連線」。
                  </li>
                </ol>
              </div>

              {/* Why & Safety Guarantees */}
              <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                <div className="flex gap-2.5 rounded-xl border border-slate-800/80 bg-slate-950/30 p-3">
                  <Zap className="h-4 w-4 shrink-0 text-amber-400" />
                  <div>
                    <h4 className="text-xs font-semibold text-slate-200">徹底解決 40+ 人塞車</h4>
                    <p className="mt-0.5 text-[11px] leading-relaxed text-slate-400">
                      學生練習頻率極高，透過本地編譯分流，伺服器 CPU 消耗降至趨近於零。
                    </p>
                  </div>
                </div>
                <div className="flex gap-2.5 rounded-xl border border-slate-800/80 bg-slate-950/30 p-3">
                  <ShieldCheck className="h-4 w-4 shrink-0 text-sky-400" />
                  <div>
                    <h4 className="text-xs font-semibold text-slate-200">作業與考試公正防弊</h4>
                    <p className="mt-0.5 text-[11px] leading-relaxed text-slate-400">
                      「正式繳交 (Submit)」與隱藏測資仍由伺服器沙盒驗證與計分，兼具效能與防作弊。
                    </p>
                  </div>
                </div>
              </div>
            </>
          ) : (
            /* Doctor Diagnostic Tab */
            <div className="space-y-4">
              <div className="flex items-center justify-between rounded-xl border border-slate-800 bg-slate-950/60 p-4">
                <div>
                  <h3 className="font-semibold text-white flex items-center gap-2">
                    <Stethoscope className="h-4 w-4 text-emerald-400" />
                    <span>本機 C/C++ 編譯工具鏈健全性診斷</span>
                  </h3>
                  <p className="text-xs text-slate-400 mt-0.5">
                    檢測電腦中的 GCC、G++ 版本、C++17 語法相容性與臨時檔案權限
                  </p>
                </div>
                <button
                  type="button"
                  onClick={handleRunDoctor}
                  disabled={runningDoctor || status !== 'connected'}
                  className={buttonClass('primary', 'gap-1.5 px-3 py-1.5 text-xs shadow-md')}
                >
                  {runningDoctor ? <Spinner className="h-3.5 w-3.5" /> : <RefreshCw className="h-3.5 w-3.5" />}
                  <span>{runningDoctor ? '診斷中…' : '執行診斷'}</span>
                </button>
              </div>

              {status !== 'connected' && (
                <div className="flex items-start gap-2.5 rounded-xl border border-amber-500/30 bg-amber-500/10 p-4 text-xs text-amber-300">
                  <AlertTriangle className="h-4 w-4 shrink-0 text-amber-400 mt-0.5" />
                  <div>
                    <span className="font-semibold">Local Agent 尚未連線</span>
                    <p className="mt-1 leading-relaxed text-amber-200/90">
                      請先切換至「下載與連線設定」分頁下載並啟動 Local Agent。啟動後點擊上方「重新偵測」，即可使用一鍵工具鏈診斷。
                    </p>
                  </div>
                </div>
              )}

              {doctorError && (
                <div className="flex items-center gap-2 rounded-xl border border-rose-500/30 bg-rose-500/10 p-3 text-xs text-rose-300">
                  <CircleAlert className="h-4 w-4 shrink-0 text-rose-400" />
                  <span>診斷發生錯誤：{doctorError}</span>
                </div>
              )}

              {doctorReport && (
                <div className="space-y-3">
                  {/* Status Pill */}
                  <div
                    className={`flex items-center justify-between rounded-xl border px-4 py-3 ${
                      doctorReport.status === 'ok'
                        ? 'border-emerald-500/40 bg-emerald-500/15 text-emerald-300'
                        : doctorReport.status === 'degraded'
                        ? 'border-amber-500/40 bg-amber-500/15 text-amber-300'
                        : 'border-rose-500/40 bg-rose-500/15 text-rose-300'
                    }`}
                  >
                    <div className="flex items-center gap-2">
                      {doctorReport.status === 'ok' ? (
                        <CircleCheck className="h-5 w-5 text-emerald-400" />
                      ) : (
                        <CircleAlert className="h-5 w-5 text-amber-400" />
                      )}
                      <div>
                        <span className="font-semibold text-sm">
                          {doctorReport.status === 'ok'
                            ? '工具鏈狀態良好 (Toolchain Ready)'
                            : doctorReport.status === 'degraded'
                            ? '部分工具鏈缺失或版本過舊 (Degraded)'
                            : '未找到編譯器 (Compiler Missing)'}
                        </span>
                        <p className="text-[11px] opacity-80">
                          作業系統: {doctorReport.os} ({doctorReport.arch}) · Python {doctorReport.python_version}
                        </p>
                      </div>
                    </div>
                  </div>

                  {/* Checklist Grid */}
                  <div className="grid grid-cols-1 gap-2.5 sm:grid-cols-2">
                    {/* C Compiler */}
                    <div className="rounded-xl border border-slate-800 bg-slate-950/40 p-3 text-xs">
                      <div className="flex items-center justify-between">
                        <span className="font-medium text-slate-300">C 編譯器 (GCC / Clang)</span>
                        {doctorReport.c_compiler.found ? (
                          <span className="flex items-center gap-1 text-emerald-400 font-mono text-[11px]">
                            <CircleCheck className="h-3.5 w-3.5" /> 正常
                          </span>
                        ) : (
                          <span className="flex items-center gap-1 text-rose-400 font-mono text-[11px]">
                            <CircleX className="h-3.5 w-3.5" /> 未找到
                          </span>
                        )}
                      </div>
                      <p className="mt-1 font-mono text-[11px] text-slate-400 truncate" title={doctorReport.c_compiler.path || ''}>
                        路徑：{doctorReport.c_compiler.path || '(無)'}
                      </p>
                      <p className="mt-0.5 font-mono text-[10px] text-slate-500 truncate" title={doctorReport.c_compiler.version}>
                        {doctorReport.c_compiler.version || '未安裝'}
                      </p>
                    </div>

                    {/* C++ Compiler */}
                    <div className="rounded-xl border border-slate-800 bg-slate-950/40 p-3 text-xs">
                      <div className="flex items-center justify-between">
                        <span className="font-medium text-slate-300">C++ 編譯器 (G++ / Clang++)</span>
                        {doctorReport.cpp_compiler.found ? (
                          <span className="flex items-center gap-1 text-emerald-400 font-mono text-[11px]">
                            <CircleCheck className="h-3.5 w-3.5" /> 正常
                          </span>
                        ) : (
                          <span className="flex items-center gap-1 text-rose-400 font-mono text-[11px]">
                            <CircleX className="h-3.5 w-3.5" /> 未找到
                          </span>
                        )}
                      </div>
                      <p className="mt-1 font-mono text-[11px] text-slate-400 truncate" title={doctorReport.cpp_compiler.path || ''}>
                        路徑：{doctorReport.cpp_compiler.path || '(無)'}
                      </p>
                      <div className="mt-1 flex items-center justify-between text-[11px]">
                        <span className="text-slate-400">C++17 標準支援：</span>
                        {doctorReport.cpp_compiler.cpp17_supported ? (
                          <span className="text-emerald-400 font-medium">支援 ✅</span>
                        ) : (
                          <span className="text-rose-400 font-medium">未支援 ❌</span>
                        )}
                      </div>
                    </div>

                    {/* Temp Directory */}
                    <div className="rounded-xl border border-slate-800 bg-slate-950/40 p-3 text-xs sm:col-span-2">
                      <div className="flex items-center justify-between">
                        <span className="font-medium text-slate-300">暫存工作區讀寫權限</span>
                        {doctorReport.temp_dir.writable ? (
                          <span className="flex items-center gap-1 text-emerald-400 font-mono text-[11px]">
                            <FolderCheck className="h-3.5 w-3.5" /> 讀寫正常
                          </span>
                        ) : (
                          <span className="flex items-center gap-1 text-rose-400 font-mono text-[11px]">
                            <FolderX className="h-3.5 w-3.5" /> 權限不足
                          </span>
                        )}
                      </div>
                      <p className="mt-1 font-mono text-[11px] text-slate-500 truncate">
                        目錄：{doctorReport.temp_dir.path}
                      </p>
                    </div>
                  </div>

                  {/* Recommendations */}
                  <div className="rounded-xl border border-slate-800 bg-slate-900/60 p-4 space-y-2.5">
                    <h4 className="text-xs font-semibold text-slate-200 flex items-center gap-1.5">
                      <Wrench className="h-3.5 w-3.5 text-sky-400" />
                      <span>診斷建議與修復指引</span>
                    </h4>
                    <ul className="space-y-1.5 text-xs text-slate-300 list-inside list-disc">
                      {doctorReport.suggestions.map((s, idx) => (
                        <li key={idx} className="leading-relaxed">
                          {s}
                        </li>
                      ))}
                    </ul>
                  </div>
                </div>
              )}

              {/* OS Quick Commands Box */}
              <div className="space-y-2 rounded-xl border border-slate-800 bg-slate-950/40 p-3.5 text-xs">
                <span className="font-semibold text-slate-200">跨平台編譯器一鍵安裝指令：</span>
                <div className="space-y-2 font-mono text-[11px]">
                  <div className="flex items-center justify-between rounded bg-slate-900 p-2 text-slate-300">
                    <div>
                      <span className="text-slate-500 font-sans mr-2">Windows:</span>
                      <code>winget install MSYS2.MSYS2</code>
                    </div>
                    <CopyButton value="winget install MSYS2.MSYS2" label="複製" />
                  </div>
                  <div className="flex items-center justify-between rounded bg-slate-900 p-2 text-slate-300">
                    <div>
                      <span className="text-slate-500 font-sans mr-2">macOS:</span>
                      <code>xcode-select --install</code>
                    </div>
                    <CopyButton value="xcode-select --install" label="複製" />
                  </div>
                  <div className="flex items-center justify-between rounded bg-slate-900 p-2 text-slate-300">
                    <div>
                      <span className="text-slate-500 font-sans mr-2">Ubuntu / Debian:</span>
                      <code>sudo apt update && sudo apt install -y build-essential</code>
                    </div>
                    <CopyButton value="sudo apt update && sudo apt install -y build-essential" label="複製" />
                  </div>
                </div>
              </div>
            </div>
          )}
        </div>

        {/* Footer */}
        <div className="flex shrink-0 items-center justify-between border-t border-slate-800 bg-slate-950/80 px-5 py-3">
          <div className="flex items-center gap-1.5 text-[11px] text-slate-500">
            <Cpu className="h-3.5 w-3.5" />
            <span>需求：Python 3.8+ 與 GCC (g++) 或 Clang (clang++)</span>
          </div>
          <button
            type="button"
            onClick={onClose}
            className={buttonClass('ghost', 'text-xs')}
          >
            關閉
          </button>
        </div>
      </div>
    </div>
  )
}
