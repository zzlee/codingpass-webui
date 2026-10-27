import { useState } from 'react'
import {
  CheckCircle2,
  Cpu,
  Download,
  Laptop,
  RefreshCw,
  ShieldCheck,
  Terminal,
  WifiOff,
  X,
  Zap,
} from 'lucide-react'

import { useLocalAgent } from '../localAgentContext'
import { buttonClass } from '../format'

export interface AgentModalProps {
  open: boolean
  onClose: () => void
}

export default function AgentModal({ open, onClose }: AgentModalProps) {
  const { status, compilerInfo, agentVersion, checkConnection } = useLocalAgent()
  const [downloading, setDownloading] = useState(false)

  if (!open) return null

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
      <div className="relative flex max-h-[90vh] w-full max-w-xl flex-col overflow-hidden rounded-2xl border border-slate-800 bg-slate-900 shadow-2xl shadow-black/60">
        {/* Header */}
        <div className="flex shrink-0 items-center justify-between border-b border-slate-800 bg-slate-950/60 px-5 py-4">
          <div className="flex items-center gap-2.5">
            <span className="grid h-9 w-9 place-items-center rounded-xl bg-gradient-to-br from-emerald-500 to-sky-500 shadow-md shadow-sky-950/50">
              <Laptop className="h-5 w-5 text-white" strokeWidth={2.2} />
            </span>
            <div>
              <h2 className="text-base font-bold text-white">CodingPass 本地加速代理 (Local Agent)</h2>
              <p className="text-xs text-slate-400">
                將日常編譯搬移到學生電腦，徹底解放課堂伺服器負載
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
                    : '未連線到本地代理 (當前使用伺服器編譯)'}
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
                    ✨ Playground 與題目範例測資皆由本機即時執行，零延遲且免排隊！
                  </p>
                </div>
              ) : (
                <p className="mt-1 text-xs text-slate-400">
                  下載並在背景執行代理程式後，網頁將自動偵測並點亮綠燈。
                </p>
              )}
            </div>
          </div>

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
                ，此網頁頂部會立即轉為「本地加速已連線」。
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
