/**
 * Teacher live proctoring dashboard:
 * Real-time monitoring of student exam sessions, fullscreen state, window blur counts,
 * agent win32 lock verification, and force-submit controls.
 */
import { useEffect, useState, useCallback } from 'react'
import {
  CheckCircle2,
  Eye,
  ShieldAlert,
  ShieldCheck,
  StopCircle,
} from 'lucide-react'

import { Modal, Spinner } from './ui'
import { api, ApiError } from '../api'
import { buttonClass, formatTimestamp } from '../format'
import type { ExamMonitorResponse, ExamSession } from '../types'

export interface ExamProctorModalProps {
  open: boolean
  onClose: () => void
  problemId: number
}

export function ExamProctorModal({ open, onClose, problemId }: ExamProctorModalProps) {
  const [data, setData] = useState<ExamMonitorResponse | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [actionBusy, setActionBusy] = useState<number | null>(null)

  const fetchMonitor = useCallback(async () => {
    if (!open) return
    setError(null)
    try {
      const res = await api.getExamMonitor(problemId)
      setData(res)
    } catch (err) {
      if (err instanceof ApiError) {
        setError(err.detail)
      } else {
        setError(err instanceof Error ? err.message : String(err))
      }
    } finally {
      setLoading(false)
    }
  }, [open, problemId])

  useEffect(() => {
    if (open) {
      setLoading(true)
      fetchMonitor()
      const timer = window.setInterval(fetchMonitor, 3000)
      return () => window.clearInterval(timer)
    }
  }, [open, fetchMonitor])

  const handleForceSubmit = async (session: ExamSession) => {
    if (actionBusy !== null) return
    if (!window.confirm(`確定要對考生 ${session.student_name} (${session.student_username}) 強制收卷嗎？`)) {
      return
    }

    setActionBusy(session.id)
    try {
      await api.forceSubmitExam(session.id)
      fetchMonitor()
    } catch (err) {
      alert(err instanceof Error ? err.message : String(err))
    } finally {
      setActionBusy(null)
    }
  }

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="即時監考儀表板 (Live Exam Proctoring)"
      subtitle={`考題: ${data?.problem_title || '載入中…'} · 每 3 秒自動更新`}
      icon={<ShieldAlert className="h-5 w-5 text-rose-400" />}
      wide
      footer={
        <div className="flex w-full items-center justify-between">
          <span className="text-xs text-slate-400">
            目前應考人數: <strong className="text-emerald-400">{data?.active_students ?? 0}</strong> 人
          </span>
          <button
            type="button"
            onClick={onClose}
            className={buttonClass('subtle')}
          >
            關閉
          </button>
        </div>
      }
    >
      <div className="space-y-4">
        {error ? (
          <div className="rounded-lg border border-rose-500/40 bg-rose-500/10 p-3 text-xs text-rose-300">
            {error}
          </div>
        ) : null}

        {/* Stats tiles */}
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 text-xs">
          <div className="rounded-xl border border-slate-800 bg-slate-950/60 p-3">
            <span className="text-slate-500 block text-[11px]">應考總人次</span>
            <span className="text-base font-bold text-slate-100 font-mono mt-0.5 block">
              {data?.sessions.length ?? 0}
            </span>
          </div>

          <div className="rounded-xl border border-slate-800 bg-slate-950/60 p-3">
            <span className="text-slate-500 block text-[11px]">即時連線中</span>
            <span className="text-base font-bold text-emerald-400 font-mono mt-0.5 block">
              {data?.active_students ?? 0}
            </span>
          </div>

          <div className="rounded-xl border border-slate-800 bg-slate-950/60 p-3">
            <span className="text-slate-500 block text-[11px]">切窗容許閾值</span>
            <span className="text-base font-bold text-amber-400 font-mono mt-0.5 block">
              {data?.max_blur_count ?? 3} 次
            </span>
          </div>

          <div className="rounded-xl border border-slate-800 bg-slate-950/60 p-3">
            <span className="text-slate-500 block text-[11px]">鎖定防護層級</span>
            <span className="text-xs font-semibold text-sky-300 mt-1 block">
              {data?.exam_lock_required ? '強制 Win32 Agent' : '全螢幕軟性監考'}
            </span>
          </div>
        </div>

        {/* Sessions table */}
        <div className="overflow-x-auto rounded-xl border border-slate-800 bg-slate-950/40">
          <table className="w-full text-left text-xs">
            <thead className="border-b border-slate-800 bg-slate-900/60 font-semibold text-slate-400">
              <tr>
                <th className="px-3.5 py-2.5">考生</th>
                <th className="px-3.5 py-2.5">狀態</th>
                <th className="px-3.5 py-2.5">Windows 鎖定</th>
                <th className="px-3.5 py-2.5">切窗/離焦</th>
                <th className="px-3.5 py-2.5">全螢幕脫離</th>
                <th className="px-3.5 py-2.5">開始時間</th>
                <th className="px-3.5 py-2.5 text-right">操作</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-800/60 font-sans">
              {!data || data.sessions.length === 0 ? (
                <tr>
                  <td colSpan={7} className="px-4 py-8 text-center text-slate-500">
                    {loading ? '載入監考資料中…' : '目前尚無考生開始此試卷。'}
                  </td>
                </tr>
              ) : (
                data.sessions.map((s) => {
                  const now = Date.now()
                  const lastBeat = new Date(s.last_heartbeat).getTime()
                  const isAlive = (now - lastBeat) < 20_000 && s.status === 'in_progress'
                  const maxBlurs = data.max_blur_count || 3
                  const isExceeded = s.blur_count > maxBlurs

                  return (
                    <tr key={s.id} className="hover:bg-slate-800/30 transition-colors">
                      <td className="px-3.5 py-2.5">
                        <div className="font-semibold text-slate-200">{s.student_name}</div>
                        <div className="font-mono text-[10px] text-slate-500">{s.student_username}</div>
                      </td>

                      <td className="px-3.5 py-2.5">
                        {s.status === 'submitted' ? (
                          <span className="inline-flex items-center gap-1 text-slate-400">
                            <CheckCircle2 className="h-3.5 w-3.5 text-slate-400" />
                            <span>已交卷</span>
                          </span>
                        ) : isAlive ? (
                          <span className="inline-flex items-center gap-1.5 text-emerald-300 font-medium">
                            <span className="h-2 w-2 rounded-full bg-emerald-400 animate-pulse" />
                            <span>應考中</span>
                          </span>
                        ) : (
                          <span className="inline-flex items-center gap-1.5 text-slate-500">
                            <span className="h-2 w-2 rounded-full bg-slate-600" />
                            <span>離線</span>
                          </span>
                        )}
                      </td>

                      <td className="px-3.5 py-2.5">
                        {s.agent_lock_verified ? (
                          <span className="inline-flex items-center gap-1 text-emerald-300">
                            <ShieldCheck className="h-3.5 w-3.5 text-emerald-400" />
                            <span>Win32 鎖定</span>
                          </span>
                        ) : (
                          <span className="inline-flex items-center gap-1 text-slate-400">
                            <Eye className="h-3.5 w-3.5 text-slate-500" />
                            <span>網頁監考</span>
                          </span>
                        )}
                      </td>

                      <td className="px-3.5 py-2.5 font-mono">
                        <span className={`inline-block px-1.5 py-0.5 rounded text-[11px] ${
                          isExceeded
                            ? 'bg-rose-500/20 text-rose-300 font-bold border border-rose-500/40'
                            : s.blur_count > 0
                            ? 'bg-amber-500/20 text-amber-300'
                            : 'text-slate-400'
                        }`}>
                          {s.blur_count} 次
                        </span>
                      </td>

                      <td className="px-3.5 py-2.5 font-mono text-slate-400">
                        {s.fullscreen_exited_count} 次
                      </td>

                      <td className="px-3.5 py-2.5 text-slate-400 text-[11px]">
                        {formatTimestamp(s.start_time)}
                      </td>

                      <td className="px-3.5 py-2.5 text-right">
                        {s.status === 'in_progress' ? (
                          <button
                            type="button"
                            onClick={() => handleForceSubmit(s)}
                            disabled={actionBusy === s.id}
                            title="強制收取該考生試卷"
                            className="inline-flex items-center gap-1 rounded-md border border-rose-500/40 bg-rose-500/10 px-2 py-1 text-[11px] font-medium text-rose-200 hover:bg-rose-500/20 transition-colors"
                          >
                            {actionBusy === s.id ? (
                              <Spinner />
                            ) : (
                              <StopCircle className="h-3 w-3 text-rose-400" />
                            )}
                            <span>強制收卷</span>
                          </button>
                        ) : (
                          <span className="text-slate-600 text-[11px]">—</span>
                        )}
                      </td>
                    </tr>
                  )
                })
              )}
            </tbody>
          </table>
        </div>
      </div>
    </Modal>
  )
}

export default ExamProctorModal
