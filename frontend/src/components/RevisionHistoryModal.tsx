import { useEffect, useMemo, useState } from 'react'
import {
  Clock,
  FileCode,
  GitCommit,
  History,
  Play,
  RotateCcw,
  Send,
  Terminal,
  Trash2,
  X,
  Check,
} from 'lucide-react'

import { buttonClass } from '../format'
import { CopyButton } from './ui'

export interface RevisionItem {
  id: string
  timestamp: number
  code: string
  language: 'c' | 'cpp'
  trigger: 'run_custom' | 'run_samples' | 'submit' | 'manual'
  lineCount: number
}

const STORAGE_PREFIX = 'codingpass_history_prob_'
const MAX_REVISIONS = 40

export function getProblemRevisions(problemId: number): RevisionItem[] {
  try {
    const raw = localStorage.getItem(`${STORAGE_PREFIX}${problemId}`)
    if (!raw) return []
    const parsed = JSON.parse(raw)
    return Array.isArray(parsed) ? parsed : []
  } catch {
    return []
  }
}

export function saveProblemRevision(
  problemId: number,
  code: string,
  language: 'c' | 'cpp',
  trigger: RevisionItem['trigger'],
): RevisionItem[] {
  if (!code.trim()) return getProblemRevisions(problemId)
  try {
    const existing = getProblemRevisions(problemId)
    // Avoid creating identical back-to-back revisions
    if (existing.length > 0 && existing[0].code === code && existing[0].language === language) {
      return existing
    }

    const lines = code.replace(/\n$/, '').split('\n').length
    const newItem: RevisionItem = {
      id: `${Date.now()}-${Math.random().toString(36).slice(2, 7)}`,
      timestamp: Date.now(),
      code,
      language,
      trigger,
      lineCount: lines,
    }

    const updated = [newItem, ...existing].slice(0, MAX_REVISIONS)
    localStorage.setItem(`${STORAGE_PREFIX}${problemId}`, JSON.stringify(updated))
    return updated
  } catch {
    return getProblemRevisions(problemId)
  }
}

export function clearProblemRevisions(problemId: number): void {
  try {
    localStorage.removeItem(`${STORAGE_PREFIX}${problemId}`)
  } catch {
    // Ignore storage errors
  }
}

function formatRelativeTime(ts: number): string {
  const diffSec = Math.floor((Date.now() - ts) / 1000)
  if (diffSec < 10) return '剛剛'
  if (diffSec < 60) return `${diffSec} 秒前`
  const diffMin = Math.floor(diffSec / 60)
  if (diffMin < 60) return `${diffMin} 分鐘前`
  const diffHours = Math.floor(diffMin / 60)
  if (diffHours < 24) return `${diffHours} 小時前`
  const date = new Date(ts)
  return `${date.getMonth() + 1}/${date.getDate()} ${String(date.getHours()).padStart(2, '0')}:${String(
    date.getMinutes(),
  ).padStart(2, '0')}`
}

function triggerInfo(trigger: RevisionItem['trigger']): { label: string; icon: typeof Terminal; badgeClass: string } {
  switch (trigger) {
    case 'run_custom':
      return {
        label: '自訂測資',
        icon: Terminal,
        badgeClass: 'border-indigo-500/40 bg-indigo-500/15 text-indigo-300',
      }
    case 'run_samples':
      return {
        label: '範例執行',
        icon: Play,
        badgeClass: 'border-sky-500/40 bg-sky-500/15 text-sky-300',
      }
    case 'submit':
      return {
        label: '送出評測',
        icon: Send,
        badgeClass: 'border-emerald-500/40 bg-emerald-500/15 text-emerald-300',
      }
    case 'manual':
      return {
        label: '手動快照',
        icon: GitCommit,
        badgeClass: 'border-amber-500/40 bg-amber-500/15 text-amber-300',
      }
  }
}

export interface RevisionHistoryModalProps {
  open: boolean
  onClose: () => void
  problemId: number
  currentCode: string
  onRestore: (code: string, language: 'c' | 'cpp') => void
}

export default function RevisionHistoryModal({
  open,
  onClose,
  problemId,
  currentCode,
  onRestore,
}: RevisionHistoryModalProps) {
  const [revisions, setRevisions] = useState<RevisionItem[]>([])
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [restoredId, setRestoredId] = useState<string | null>(null)

  useEffect(() => {
    if (open) {
      const items = getProblemRevisions(problemId)
      setRevisions(items)
      if (items.length > 0) {
        setSelectedId(items[0].id)
      } else {
        setSelectedId(null)
      }
      setRestoredId(null)
    }
  }, [open, problemId])

  const selectedItem = useMemo(() => {
    if (!selectedId) return revisions[0] || null
    return revisions.find((item) => item.id === selectedId) || null
  }, [selectedId, revisions])

  if (!open) return null

  const handleClear = () => {
    if (window.confirm('確定要清除此題目的所有本機版本歷史嗎？（清除後無法復原）')) {
      clearProblemRevisions(problemId)
      setRevisions([])
      setSelectedId(null)
    }
  }

  const handleRestore = (item: RevisionItem) => {
    onRestore(item.code, item.language)
    setRestoredId(item.id)
    setTimeout(() => {
      onClose()
    }, 400)
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/80 p-4 backdrop-blur-sm">
      <div className="flex h-[90vh] max-h-[800px] w-full max-w-5xl flex-col rounded-2xl border border-slate-800 bg-slate-900 shadow-2xl shadow-slate-950 overflow-hidden">
        {/* Modal Header */}
        <div className="flex shrink-0 items-center justify-between border-b border-slate-800 px-5 py-3.5 bg-slate-950/70">
          <div className="flex items-center gap-2.5">
            <span className="grid h-8 w-8 place-items-center rounded-lg bg-indigo-500/15 border border-indigo-500/30 text-indigo-400">
              <History className="h-4 w-4" />
            </span>
            <div>
              <div className="flex items-center gap-2">
                <h3 className="text-sm font-semibold text-slate-100">程式碼版本歷程與還原</h3>
                <span className="rounded-full bg-slate-800 px-2 py-0.5 font-mono text-[11px] text-slate-400">
                  {revisions.length} 個快照
                </span>
              </div>
              <p className="text-[11px] text-slate-400">
                每次自訂測資執行、範例測試或送出評測時自動於本機儲存，保護程式碼不丟失。
              </p>
            </div>
          </div>
          <div className="flex items-center gap-2">
            {revisions.length > 0 && (
              <button
                type="button"
                onClick={handleClear}
                title="清空此題目的所有歷史版本"
                className="inline-flex items-center gap-1 rounded-md border border-slate-800 bg-slate-800/60 px-2 py-1 text-xs text-slate-400 hover:border-rose-500/40 hover:text-rose-300"
              >
                <Trash2 className="h-3 w-3" />
                <span>清空歷程</span>
              </button>
            )}
            <button
              type="button"
              onClick={onClose}
              className="rounded-lg p-1.5 text-slate-400 hover:bg-slate-800 hover:text-white"
            >
              <X className="h-4 w-4" />
            </button>
          </div>
        </div>

        {/* Modal Body */}
        {revisions.length === 0 ? (
          <div className="flex flex-1 flex-col items-center justify-center p-8 text-center text-slate-400">
            <Clock className="h-12 w-12 text-slate-600 mb-3" />
            <h4 className="text-sm font-medium text-slate-300">尚無歷史版本快照</h4>
            <p className="mt-1 max-w-sm text-xs text-slate-500">
              當您執行自訂測資、執行範例或送出評測時，系統將自動快照當前程式碼。
            </p>
          </div>
        ) : (
          <div className="grid min-h-0 flex-1 grid-cols-1 md:grid-cols-12 divide-y md:divide-y-0 md:divide-x divide-slate-800">
            {/* Left Column: Revision Timeline List (4 columns) */}
            <div className="flex flex-col md:col-span-4 min-h-0 overflow-y-auto bg-slate-950/40 p-2 space-y-1">
              {revisions.map((item, idx) => {
                const isSelected = selectedItem?.id === item.id
                const isIdenticalToCurrent = item.code === currentCode
                const tInfo = triggerInfo(item.trigger)
                const TIcon = tInfo.icon
                const dateObj = new Date(item.timestamp)
                const timeStr = dateObj.toLocaleTimeString('zh-TW', { hour: '2-digit', minute: '2-digit', second: '2-digit' })

                return (
                  <button
                    key={item.id}
                    type="button"
                    onClick={() => setSelectedId(item.id)}
                    className={`group w-full text-left rounded-xl p-2.5 transition-all flex flex-col gap-1 border ${
                      isSelected
                        ? 'border-indigo-500/60 bg-indigo-500/10 shadow-sm'
                        : 'border-transparent hover:border-slate-800 hover:bg-slate-850/60'
                    }`}
                  >
                    <div className="flex items-center justify-between gap-1">
                      <div className="flex items-center gap-1.5">
                        <span
                          className={`inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-[10px] font-medium border ${tInfo.badgeClass}`}
                        >
                          <TIcon className="h-2.5 w-2.5" />
                          <span>{tInfo.label}</span>
                        </span>
                        <span className="font-mono text-[10px] uppercase font-bold text-slate-400">
                          {item.language}
                        </span>
                        {idx === 0 && (
                          <span className="rounded bg-sky-500/20 px-1 py-0.2 font-mono text-[9px] text-sky-300">
                            最新
                          </span>
                        )}
                      </div>
                      <span className="text-[11px] font-mono text-slate-400">
                        {formatRelativeTime(item.timestamp)}
                      </span>
                    </div>

                    <div className="flex items-center justify-between text-[11px] text-slate-400 mt-0.5">
                      <span className="font-mono text-slate-500">{timeStr}</span>
                      <div className="flex items-center gap-2">
                        {isIdenticalToCurrent && (
                          <span className="text-[10px] text-emerald-400 font-medium">（目前編輯中代碼）</span>
                        )}
                        <span className="font-mono text-[10px] text-slate-400">{item.lineCount} 行</span>
                      </div>
                    </div>
                  </button>
                )
              })}
            </div>

            {/* Right Column: Code Preview & Restore Action (8 columns) */}
            <div className="flex flex-col md:col-span-8 min-h-0 bg-slate-900/60 overflow-hidden">
              {selectedItem ? (
                <>
                  {/* Preview Toolbar */}
                  <div className="flex shrink-0 items-center justify-between border-b border-slate-800 px-4 py-2.5 bg-slate-950/80">
                    <div className="flex items-center gap-2">
                      <FileCode className="h-4 w-4 text-indigo-400" />
                      <span className="font-mono text-xs font-semibold text-slate-200">
                        快照代碼預覽 ({selectedItem.language.toUpperCase()})
                      </span>
                      <span className="font-mono text-[11px] text-slate-400">
                        • {selectedItem.lineCount} 行
                      </span>
                    </div>
                    <div className="flex items-center gap-2">
                      <CopyButton value={selectedItem.code} label="複製代碼" />
                      <button
                        type="button"
                        onClick={() => handleRestore(selectedItem)}
                        disabled={restoredId === selectedItem.id}
                        className={buttonClass(
                          'primary',
                          'gap-1 text-xs bg-indigo-600 hover:bg-indigo-500 border-indigo-500 text-white shadow-sm',
                        )}
                      >
                        {restoredId === selectedItem.id ? (
                          <>
                            <Check className="h-3.5 w-3.5" />
                            <span>已還原！</span>
                          </>
                        ) : (
                          <>
                            <RotateCcw className="h-3.5 w-3.5" />
                            <span>還原至此版本</span>
                          </>
                        )}
                      </button>
                    </div>
                  </div>

                  {/* Code Viewer */}
                  <div className="flex-1 min-h-0 overflow-y-auto p-4 font-mono text-xs text-slate-200 bg-[#070b16] selection:bg-indigo-500/30">
                    <pre className="leading-relaxed whitespace-pre font-mono">
                      {selectedItem.code.split('\n').map((line, idx) => (
                        <div key={idx} className="flex">
                          <span className="w-10 shrink-0 select-none text-right pr-4 text-slate-600 font-mono text-[11px]">
                            {idx + 1}
                          </span>
                          <span className="flex-1 break-all">{line || ' '}</span>
                        </div>
                      ))}
                    </pre>
                  </div>
                </>
              ) : (
                <div className="flex flex-1 items-center justify-center text-slate-500 text-xs">
                  請選擇左側版本進行預覽
                </div>
              )}
            </div>
          </div>
        )}

        {/* Modal Footer */}
        <div className="flex shrink-0 items-center justify-between border-t border-slate-800 bg-slate-950/80 px-5 py-2.5">
          <span className="text-[11px] text-slate-500">
            * 版本快照存放於瀏覽器本機儲存空間 (localStorage)，即使離線或重整頁面依然完整保留。
          </span>
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
