import { useEffect, useState } from 'react'
import {
  AlertTriangle,
  Info,
  ShieldAlert,
  ShieldCheck,
  X,
} from 'lucide-react'

import { api } from '../api'
import { buttonClass } from '../format'
import { ErrorNotice, LoadingRow } from './ui'
import type { PlagiarismReport } from '../types'

export interface PlagiarismModalProps {
  open: boolean
  onClose: () => void
  classroomId: number
  classroomName: string
  problemId: number
  problemTitle: string
}

export default function PlagiarismModal({
  open,
  onClose,
  classroomId,
  classroomName,
  problemId,
  problemTitle,
}: PlagiarismModalProps) {
  const [threshold, setThreshold] = useState(0.85)
  const [includeExempted, setIncludeExempted] = useState(true)
  const [report, setReport] = useState<PlagiarismReport | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const loadReport = async () => {
    setLoading(true)
    setError(null)
    try {
      const data = await api.getPlagiarismReport(classroomId, problemId, {
        threshold,
        include_exempted: includeExempted,
      })
      setReport(data)
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    if (open) {
      void loadReport()
    }
  }, [open, threshold, includeExempted])

  if (!open) return null

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm p-4 overflow-y-auto">
      <div className="relative w-full max-w-3xl rounded-2xl bg-slate-900 border border-slate-700/80 shadow-2xl p-6 text-slate-200 max-h-[90vh] flex flex-col">
        <button
          type="button"
          onClick={onClose}
          className="absolute top-4 right-4 p-1.5 text-slate-400 hover:text-white rounded-lg hover:bg-slate-800 transition"
        >
          <X className="w-5 h-5" />
        </button>

        {/* Header */}
        <div className="flex items-center gap-3 mb-4">
          <div className="p-2.5 rounded-xl bg-rose-500/10 text-rose-400 border border-rose-500/20">
            <ShieldAlert className="w-5 h-5" />
          </div>
          <div>
            <h2 className="text-lg font-bold text-white tracking-tight">
              程式碼結構查重審查 (Plagiarism Screen)
            </h2>
            <p className="text-xs text-slate-400">
              {classroomName} · 題目：{problemTitle} (AST 結構比對 + 標準標碼基線扣除)
            </p>
          </div>
        </div>

        {/* Filter bar */}
        <div className="flex flex-wrap items-center justify-between gap-3 p-3 rounded-xl bg-slate-950/60 border border-slate-800 text-xs mb-4">
          <div className="flex items-center gap-3">
            <span className="text-slate-400 font-medium">相似度門檻：</span>
            <input
              type="range"
              min={0.5}
              max={1.0}
              step={0.05}
              value={threshold}
              onChange={(e) => setThreshold(Number(e.target.value))}
              className="w-28 cursor-pointer accent-indigo-500"
            />
            <span className="font-mono font-bold text-indigo-400">
              {Math.round(threshold * 100)}%
            </span>
          </div>

          <label className="flex items-center gap-2 cursor-pointer text-slate-300">
            <input
              type="checkbox"
              checked={includeExempted}
              onChange={(e) => setIncludeExempted(e.target.checked)}
              className="rounded border-slate-700 text-indigo-600 focus:ring-indigo-500 w-3.5 h-3.5"
            />
            <span>顯示標準範本解答免除項目 (Exempted)</span>
          </label>
        </div>

        {error ? <ErrorNotice message={error} /> : null}

        {/* Summary stats */}
        {report ? (
          <div className="grid grid-cols-4 gap-2.5 mb-4 text-center text-xs">
            <div className="p-2.5 rounded-xl bg-slate-950/80 border border-slate-800">
              <div className="text-slate-400 text-[11px] mb-0.5">比對人數 / 提交數</div>
              <div className="font-mono font-bold text-white">
                {report.students_compared} 人 / {report.submissions_compared} 份
              </div>
            </div>
            <div className="p-2.5 rounded-xl bg-rose-500/10 border border-rose-500/30">
              <div className="text-rose-300 text-[11px] mb-0.5">高相似度警示</div>
              <div className="font-mono font-bold text-rose-400 text-base">
                {report.flagged_count} 組
              </div>
            </div>
            <div className="p-2.5 rounded-xl bg-emerald-500/10 border border-emerald-500/30">
              <div className="text-emerald-300 text-[11px] mb-0.5">標準解答免除</div>
              <div className="font-mono font-bold text-emerald-400 text-base">
                {report.exempt_count} 組
              </div>
            </div>
            <div className="p-2.5 rounded-xl bg-slate-950/80 border border-slate-800">
              <div className="text-slate-400 text-[11px] mb-0.5">空白模板略過</div>
              <div className="font-mono font-bold text-slate-300">
                {report.skipped_trivial} 份
              </div>
            </div>
          </div>
        ) : null}

        {/* Pair list */}
        <div className="flex-1 overflow-y-auto space-y-2.5 pr-1 min-h-[200px]">
          {loading ? (
            <LoadingRow label="正在進行 AST 語法結構與 3-gram 特徵比對..." />
          ) : report && report.pairs.length > 0 ? (
            report.pairs.map((pair, idx) => (
              <div
                key={idx}
                className={`p-3.5 rounded-xl border transition ${
                  pair.flagged
                    ? 'bg-rose-950/20 border-rose-500/40'
                    : 'bg-slate-950/40 border-slate-800/80'
                }`}
              >
                <div className="flex items-center justify-between gap-2 mb-2">
                  <div className="flex items-center gap-2">
                    <span className="font-semibold text-slate-100 text-xs">
                      {pair.student_a}
                    </span>
                    <span className="text-slate-500 text-xs">vs</span>
                    <span className="font-semibold text-slate-100 text-xs">
                      {pair.student_b}
                    </span>
                  </div>

                  {pair.flagged ? (
                    <span className="inline-flex items-center gap-1 rounded-full border border-rose-500/40 bg-rose-500/20 px-2 py-0.5 text-[10px] font-bold text-rose-300 uppercase tracking-wide">
                      <ShieldAlert className="w-3 h-3" />
                      FLAGGED · 抄襲嫌疑
                    </span>
                  ) : (
                    <span className="inline-flex items-center gap-1 rounded-full border border-emerald-500/40 bg-emerald-500/10 px-2 py-0.5 text-[10px] font-semibold text-emerald-300">
                      <ShieldCheck className="w-3 h-3" />
                      EXEMPT · 範本免除
                    </span>
                  )}
                </div>

                <div className="grid grid-cols-4 gap-2 text-[11px] font-mono bg-slate-900/60 p-2 rounded-lg border border-slate-800 mb-2">
                  <div>
                    <span className="text-slate-500">綜合相似度: </span>
                    <strong className="text-white">
                      {Math.round(pair.combined_similarity * 100)}%
                    </strong>
                  </div>
                  <div>
                    <span className="text-slate-500">AST結構: </span>
                    <span className="text-slate-300">
                      {Math.round(pair.ast_similarity * 100)}%
                    </span>
                  </div>
                  <div>
                    <span className="text-slate-500">原始Token: </span>
                    <span className="text-slate-300">
                      {Math.round(pair.token_similarity * 100)}%
                    </span>
                  </div>
                  <div>
                    <span className="text-slate-500">3-gram: </span>
                    <span className="text-slate-300">
                      {Math.round(pair.ngram_jaccard * 100)}%
                    </span>
                  </div>
                </div>

                {pair.exemption ? (
                  <p className="text-[11px] text-emerald-400/90 flex items-start gap-1.5">
                    <Info className="w-3.5 h-3.5 shrink-0 mt-0.5" />
                    <span>{pair.exemption}</span>
                  </p>
                ) : pair.flagged ? (
                  <p className="text-[11px] text-rose-300 flex items-start gap-1.5">
                    <AlertTriangle className="w-3.5 h-3.5 shrink-0 mt-0.5 text-rose-400" />
                    <span>
                      程式結構高度雷同（即使更換變數名稱或調換陳述順序亦被 AST 鎖定），建議檢閱學生原始碼。
                    </span>
                  </p>
                ) : null}
              </div>
            ))
          ) : (
            <div className="py-12 text-center text-slate-500 text-xs">
              在門檻 {Math.round(threshold * 100)}% 下未發現可疑相似程式碼組。
            </div>
          )}
        </div>

        {/* Footer */}
        <div className="flex items-center justify-between pt-4 border-t border-slate-800 text-xs text-slate-500 mt-2">
          <span>採用 AST 抽象語法樹正規化、3-Gram Jaccard 與標碼基線扣除演算</span>
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
