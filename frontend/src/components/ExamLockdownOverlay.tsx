/**
 * ExamLockdownOverlay:
 * Enforces fullscreen window, monitors tab/window blur events, coordinates with
 * the Windows Local Agent keyboard hook, and streams telemetry heartbeats to the server.
 */
import { useEffect, useRef, useState, useCallback } from 'react'
import {
  AlertTriangle,
  CircleAlert,
  Eye,
  Lock,
  Maximize2,
  RefreshCw,
  Shield,
  ShieldAlert,
  ShieldCheck,
} from 'lucide-react'

import { api, ApiError } from '../api'
import { useLocalAgent } from '../localAgentContext'
import { buttonClass } from '../format'
import type { ExamHeartbeatResponse, ExamSession, ProblemDetail } from '../types'

export interface ExamLockdownOverlayProps {
  problem: ProblemDetail
  onSessionReady?: (session: ExamSession) => void
  onForceSubmitted?: () => void
}

export function ExamLockdownOverlay({
  problem,
  onSessionReady,
  onForceSubmitted,
}: ExamLockdownOverlayProps) {
  const {
    status: agentStatus,
    examLocked,
    startExamLock,
    releaseExamLock,
    heartbeatExamLock,
  } = useLocalAgent()

  const [session, setSession] = useState<ExamSession | null>(null)
  const [isFullscreen, setIsFullscreen] = useState<boolean>(Boolean(document.fullscreenElement))
  const [blurCount, setBlurCount] = useState<number>(0)
  const [warningMessage, setWarningMessage] = useState<string | null>(null)
  const [fullscreenAlert, setFullscreenAlert] = useState<boolean>(false)
  const [, setIsInitializing] = useState<boolean>(true)
  const [initError, setInitError] = useState<string | null>(null)

  const heartbeatTimer = useRef<number | null>(null)
  const pendingViolations = useRef<string[]>([])

  // 1. Initialize or resume exam session
  const initSession = useCallback(async () => {
    setIsInitializing(true)
    setInitError(null)
    try {
      let agentToken: string | undefined = undefined
      if (agentStatus === 'connected') {
        try {
          let ticket: string | undefined = undefined
          try {
            const ticketRes = await api.getExamTicket(problem.id)
            ticket = ticketRes.ticket
          } catch (tErr) {
            console.warn('Could not fetch exam ticket', tErr)
          }
          const lockRes = await startExamLock(ticket, 180)
          if (lockRes.locked) {
            agentToken = lockRes.token
          }
        } catch (err) {
          console.warn('Could not engage agent lock', err)
        }
      }

      const sess = await api.startExamSession(problem.id, {
        agent_lock_token: agentToken,
      })
      setSession(sess)
      setBlurCount(sess.blur_count)
      if (onSessionReady) onSessionReady(sess)
    } catch (err) {
      if (err instanceof ApiError) {
        setInitError(err.detail)
      } else {
        setInitError(err instanceof Error ? err.message : String(err))
      }
    } finally {
      setIsInitializing(false)
    }
  }, [problem.id, agentStatus, startExamLock, onSessionReady])

  useEffect(() => {
    initSession()
  }, [initSession])

  // 2. Request Fullscreen helper
  const enterFullscreen = async () => {
    try {
      await document.documentElement.requestFullscreen()
      setIsFullscreen(true)
      setFullscreenAlert(false)
      // Try to engage Keyboard Lock API (supported on Chromium full-screen)
      if ('keyboard' in navigator && (navigator as any).keyboard?.lock) {
        await (navigator as any).keyboard.lock([
          'Escape',
          'Tab',
          'AltLeft',
          'AltRight',
          'MetaLeft',
          'MetaRight',
        ])
      }
    } catch (err) {
      console.warn('Failed to enter fullscreen:', err)
    }
  }

  // 3. Monitor window focus & fullscreen changes
  useEffect(() => {
    const handleFullscreenChange = () => {
      const active = Boolean(document.fullscreenElement)
      setIsFullscreen(active)
      if (!active) {
        setFullscreenAlert(true)
        pendingViolations.current.push('FULLSCREEN_EXITED')
      }
    }

    const handleVisibilityChange = () => {
      if (document.hidden) {
        setBlurCount((prev) => prev + 1)
        pendingViolations.current.push('TAB_SWITCHED_OR_HIDDEN')
      }
    }

    const handleWindowBlur = () => {
      setBlurCount((prev) => prev + 1)
      pendingViolations.current.push('WINDOW_BLUR_LOST_FOCUS')
    }

    document.addEventListener('fullscreenchange', handleFullscreenChange)
    document.addEventListener('visibilitychange', handleVisibilityChange)
    window.addEventListener('blur', handleWindowBlur)

    return () => {
      document.removeEventListener('fullscreenchange', handleFullscreenChange)
      document.removeEventListener('visibilitychange', handleVisibilityChange)
      window.removeEventListener('blur', handleWindowBlur)
    }
  }, [])

  // 4. Periodic heartbeat (every 3s)
  useEffect(() => {
    if (!session || session.status !== 'in_progress') return

    const sendHeartbeat = async () => {
      const eventToReport = pendingViolations.current.shift()
      try {
        if (agentStatus === 'connected' && examLocked) {
          void heartbeatExamLock()
        }

        const res: ExamHeartbeatResponse = await api.examHeartbeat(session.id, {
          is_fullscreen: Boolean(document.fullscreenElement),
          blur_increment: 0,
          agent_locked: examLocked,
          violation_event: eventToReport,
        })

        setBlurCount(res.blur_count)
        if (res.warning) {
          setWarningMessage(res.warning)
        }

        if (res.status === 'terminated' || res.status === 'submitted') {
          if (onForceSubmitted) onForceSubmitted()
        }
      } catch (err) {
        console.warn('Heartbeat error:', err)
      }
    }

    heartbeatTimer.current = window.setInterval(sendHeartbeat, 3000)
    return () => {
      if (heartbeatTimer.current !== null) {
        window.clearInterval(heartbeatTimer.current)
      }
    }
  }, [session, agentStatus, examLocked, heartbeatExamLock, onForceSubmitted])

  // Release lock on unmount
  useEffect(() => {
    return () => {
      if (examLocked) {
        void releaseExamLock()
      }
    }
  }, [examLocked, releaseExamLock])

  // If session requires local agent and it failed
  if (initError) {
    return (
      <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/95 p-6 backdrop-blur-md">
        <div className="max-w-md rounded-2xl border border-rose-500/50 bg-slate-900 p-6 text-center shadow-2xl">
          <ShieldAlert className="mx-auto h-12 w-12 text-rose-400 mb-4" />
          <h3 className="text-lg font-bold text-slate-100">考試鎖定模式啟動失敗</h3>
          <p className="mt-2 text-sm text-slate-400">{initError}</p>
          <div className="mt-6 flex justify-center gap-3">
            <button
              type="button"
              onClick={initSession}
              className={buttonClass('primary')}
            >
              <RefreshCw className="h-4 w-4" />
              <span>重試進入</span>
            </button>
          </div>
        </div>
      </div>
    )
  }

  // Not in Fullscreen -> Hard Blocking Curtain
  if (!isFullscreen) {
    return (
      <div className="fixed inset-0 z-50 flex flex-col items-center justify-center bg-slate-950/95 p-6 backdrop-blur-md">
        <div className="max-w-lg w-full rounded-2xl border border-sky-500/40 bg-slate-900 p-6 sm:p-8 text-center shadow-2xl space-y-5">
          <div className="grid h-16 w-16 place-items-center rounded-2xl bg-sky-500/10 border border-sky-500/30 mx-auto text-sky-400">
            <Maximize2 className="h-8 w-8 animate-pulse" />
          </div>

          <div>
            <span className="rounded-full bg-amber-500/20 border border-amber-500/40 px-2.5 py-1 text-xs font-semibold text-amber-300">
              線上應考環境鎖定中
            </span>
            <h2 className="text-xl font-bold text-white mt-2">
              {problem.title} · 學生考試全畫面模式
            </h2>
            <p className="text-xs text-slate-400 mt-1">
              為確保考場公正性，考試全程必須以全畫面視窗應考。
            </p>
          </div>

          <div className="rounded-xl border border-slate-800 bg-slate-950/60 p-4 text-left text-xs space-y-2.5 text-slate-300">
            <div className="flex items-start gap-2.5">
              <Eye className="h-4 w-4 text-sky-400 shrink-0 mt-0.5" />
              <span><strong>視窗離焦監控</strong>：切換視窗、最小化或分頁切換將即時通報監考老師。</span>
            </div>
            <div className="flex items-start gap-2.5">
              <Shield className="h-4 w-4 text-emerald-400 shrink-0 mt-0.5" />
              <span>
                <strong>Windows 本地代理</strong>：
                {agentStatus === 'connected' ? (
                  <span className="text-emerald-300">已就緒 (將硬性攔截 Win 鍵與 Alt+Tab)</span>
                ) : (
                  <span className="text-amber-300">未連線 (建議啟動 codingpass-agent 達成物理防弊)</span>
                )}
              </span>
            </div>
            <div className="flex items-start gap-2.5">
              <Lock className="h-4 w-4 text-rose-400 shrink-0 mt-0.5" />
              <span><strong>禁止外部貼上與 AI 輔助</strong>：題目禁用複製，AI Tutor 面板在考試期間已關閉。</span>
            </div>
          </div>

          {fullscreenAlert ? (
            <div className="flex items-center gap-2 rounded-lg border border-rose-500/40 bg-rose-500/15 p-3 text-xs text-rose-300 text-left">
              <CircleAlert className="h-4 w-4 shrink-0 text-rose-400" />
              <span>偵測到脫離全螢幕！請立即點擊下方按鈕恢復全螢幕。</span>
            </div>
          ) : null}

          <button
            type="button"
            onClick={enterFullscreen}
            className={`w-full py-3 text-base ${buttonClass('primary')}`}
          >
            <Maximize2 className="h-5 w-5" />
            <span>進入全螢幕並開始考試</span>
          </button>
        </div>
      </div>
    )
  }

  // In Fullscreen -> Top floating status banner
  const maxBlurs = problem.max_blur_count || 3
  const isHighAlert = blurCount >= maxBlurs

  return (
    <>
      {/* Floating Status Pill */}
      <aside aria-label="Exam status bar" className="fixed top-3 left-1/2 -translate-x-1/2 z-40 flex items-center gap-3 rounded-full border border-slate-700/80 bg-slate-900/95 px-4 py-1.5 shadow-xl backdrop-blur text-xs">
        <div className="flex items-center gap-1.5 font-semibold text-sky-300">
          <span className="h-2 w-2 rounded-full bg-emerald-400 animate-pulse" />
          <span>考試進行中</span>
        </div>

        <div className="h-3 w-px bg-slate-700" />

        <div className={`flex items-center gap-1.5 font-mono ${isHighAlert ? 'text-rose-400 font-bold' : 'text-slate-300'}`}>
          <AlertTriangle className={`h-3.5 w-3.5 ${isHighAlert ? 'text-rose-400' : 'text-slate-400'}`} />
          <span>切窗/離焦: {blurCount} / {maxBlurs}</span>
        </div>

        <div className="h-3 w-px bg-slate-700" />

        <div className="flex items-center gap-1.5 text-slate-400">
          {examLocked ? (
            <span className="inline-flex items-center gap-1 text-emerald-300 font-medium" title="Win32 底層鍵盤鉤子已鎖定 Win 鍵與 Alt+Tab">
              <ShieldCheck className="h-3.5 w-3.5 text-emerald-400" />
              <span>Win 鎖定</span>
            </span>
          ) : (
            <span className="inline-flex items-center gap-1 text-slate-400" title="純網頁全螢幕軟性監理">
              <Eye className="h-3.5 w-3.5" />
              <span>網頁全螢幕</span>
            </span>
          )}
        </div>
      </aside>

      {/* Warning Toast if limit approached or exceeded */}
      {warningMessage ? (
        <div
          role="alert"
          className="fixed bottom-6 right-6 z-40 flex items-start gap-3 max-w-sm rounded-xl border border-rose-500/50 bg-rose-950/90 p-4 text-xs text-rose-200 shadow-2xl backdrop-blur animate-bounce"
        >
          <CircleAlert className="h-5 w-5 shrink-0 text-rose-400" />
          <div className="space-y-1">
            <p className="font-bold">違規次數警告</p>
            <p>{warningMessage}</p>
          </div>
        </div>
      ) : null}
    </>
  )
}

export default ExamLockdownOverlay
