import { useState } from 'react'
import {
  Bot,
  CheckCircle2,
  Code2,
  Download,
  FileCheck,
  FileCode2,
  FileText,
  Loader2,
  Sparkles,
  Wand2,
  X,
  AlertTriangle,
  Play,
  Layers,
} from 'lucide-react'
import api from '../api'
import type { AIGenerateProblemResponse, Classroom, Difficulty } from '../types'
import { buttonClass } from '../format'

interface AIGenerateProblemModalProps {
  open: boolean
  onClose: () => void
  classrooms?: Classroom[]
  defaultClassroomId?: number | null
  onProblemCreated?: (problemId: number) => void
}

const PRESET_PROMPTS = [
  {
    label: '雙指標區間搜尋',
    difficulty: 'medium' as Difficulty,
    prompt: '請出一個雙指標或滑動視窗的 C++ 題目：給定正整數陣列與目標值 S，求和大於等於 S 的最短連續子陣列長度。若不存在輸出 0。輸入多筆直到 EOF。包含極端邊界與大陣列測資。',
  },
  {
    label: '區間質數與因數分解',
    difficulty: 'easy' as Difficulty,
    prompt: '請出一個計算區間內質數個數與最大質因數的 C++ 題目。輸入兩正整數 A 與 B (1 <= A <= B <= 100000)，輸出該區間內的質數數量。讀到 EOF。邊界需涵蓋包含 1、無質數及大區間情況。',
  },
  {
    label: '動態規劃：0/1 背包',
    difficulty: 'medium' as Difficulty,
    prompt: '請出一個 0/1 背包問題的變形題：旅行者背包承重限制 W，有 N 件物品每件有重量與價值，並限制最多只能選取 K 件物品，求最大總價值。輸入多筆直到 EOF。',
  },
  {
    label: '拓撲排序與環檢測',
    difficulty: 'hard' as Difficulty,
    prompt: '請出一個有向圖選課順序的 C++ 題目：給定 N 門課程與 M 組先修關係，輸出字典序最小的合法修課順序；若圖中存在環導致無法修完，輸出 -1。輸入多筆直到 EOF。',
  },
]

export default function AIGenerateProblemModal({
  open,
  onClose,
  classrooms = [],
  defaultClassroomId = null,
  onProblemCreated,
}: AIGenerateProblemModalProps) {
  const [prompt, setPrompt] = useState(PRESET_PROMPTS[0].prompt)
  const [difficulty, setDifficulty] = useState<Difficulty>('medium')
  const [problemCode, setProblemCode] = useState('')
  const [classroomId, setClassroomId] = useState<string>(
    defaultClassroomId ? String(defaultClassroomId) : '',
  )
  const [isExam, setIsExam] = useState(false)
  const [examLockRequired, setExamLockRequired] = useState(false)
  const [autoImport, setAutoImport] = useState(true)
  const [numRandomCases, setNumRandomCases] = useState(30)

  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [result, setResult] = useState<AIGenerateProblemResponse | null>(null)
  const [activeTab, setActiveTab] = useState<'statement' | 'ref_code' | 'teda' | 'rules' | 'samples'>('statement')

  if (!open) return null

  const handleGenerate = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!prompt.trim()) return

    setLoading(true)
    setError(null)
    setResult(null)

    try {
      const resp = await api.aiGenerateProblem({
        prompt: prompt.trim(),
        difficulty,
        problem_code: problemCode.trim() || undefined,
        classroom_id: classroomId ? Number(classroomId) : undefined,
        is_exam: isExam,
        exam_lock_required: examLockRequired,
        auto_import: autoImport,
        num_random_cases: numRandomCases,
      })
      setResult(resp)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setLoading(false)
    }
  }

  const handleApplyPreset = (p: typeof PRESET_PROMPTS[0]) => {
    setPrompt(p.prompt)
    setDifficulty(p.difficulty)
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/80 p-4 backdrop-blur-sm"
      role="dialog"
      aria-modal="true"
    >
      <div className="relative flex max-h-[92vh] w-full max-w-4xl flex-col rounded-2xl border border-slate-700/80 bg-slate-900 shadow-2xl">
        {/* Header */}
        <div className="flex items-center justify-between border-b border-slate-800 px-6 py-4">
          <div className="flex items-center gap-3">
            <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-gradient-to-br from-indigo-500 to-purple-600 text-white shadow-lg shadow-indigo-500/20">
              <Bot className="h-5 w-5" />
            </div>
            <div>
              <div className="flex items-center gap-2">
                <h2 className="text-base font-semibold text-slate-100">CodingPass AI 智慧出題 Agent</h2>
                <span className="inline-flex items-center gap-1 rounded-full bg-indigo-500/10 px-2 py-0.5 text-xs font-medium text-indigo-400 border border-indigo-500/20">
                  <Sparkles className="h-3 w-3" />
                  Pi + Qwen3.8 Flash
                </span>
              </div>
              <p className="text-xs text-slate-400">
                依據 TeDa 出題指南規格，自動生成 C++ 標竿正解、隨機測資腳本、語量規則與題本
              </p>
            </div>
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close dialog"
            className="rounded-lg p-1.5 text-slate-400 hover:bg-slate-800 hover:text-slate-200"
          >
            <X className="h-5 w-5" />
          </button>
        </div>

        {/* Body content */}
        <div className="flex-1 overflow-y-auto px-6 py-4 space-y-5">
          {!result ? (
            <form onSubmit={handleGenerate} className="space-y-4">
              {/* Presets */}
              <div>
                <label className="mb-1.5 block text-xs font-medium text-slate-400">
                  快速範本主題
                </label>
                <div className="flex flex-wrap gap-2">
                  {PRESET_PROMPTS.map((p, idx) => (
                    <button
                      key={idx}
                      type="button"
                      onClick={() => handleApplyPreset(p)}
                      className="rounded-lg border border-slate-700/80 bg-slate-800/60 px-2.5 py-1 text-xs text-slate-300 hover:border-indigo-500 hover:bg-slate-800 hover:text-white transition"
                    >
                      {p.label}
                    </button>
                  ))}
                </div>
              </div>

              {/* Natural Language Prompt */}
              <div>
                <label className="mb-1.5 block text-xs font-medium text-slate-300">
                  出題自然語言規格與需求描述 <span className="text-rose-400">*</span>
                </label>
                <textarea
                  value={prompt}
                  onChange={(e) => setPrompt(e.target.value)}
                  rows={4}
                  required
                  placeholder="請清楚描述題目情境、演算法核心要求、輸入與輸出規範、邊界數值限制..."
                  className="w-full rounded-xl border border-slate-700 bg-slate-950 px-3.5 py-2.5 text-sm text-slate-100 placeholder-slate-500 focus:border-indigo-500 focus:outline-none focus:ring-1 focus:ring-indigo-500"
                />
              </div>

              {/* Configurations Row */}
              <div className="grid grid-cols-1 gap-3 sm:grid-cols-4">
                <div>
                  <label className="mb-1 block text-xs font-medium text-slate-400">題目難度</label>
                  <select
                    value={difficulty}
                    onChange={(e) => setDifficulty(e.target.value as Difficulty)}
                    className="w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-xs text-slate-200 focus:border-indigo-500 focus:outline-none"
                  >
                    <option value="easy">Easy (初階基礎)</option>
                    <option value="medium">Medium (中階演算法)</option>
                    <option value="hard">Hard (進階競程)</option>
                  </select>
                </div>

                <div>
                  <label className="mb-1 block text-xs font-medium text-slate-400">發布班級</label>
                  <select
                    value={classroomId}
                    onChange={(e) => setClassroomId(e.target.value)}
                    className="w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-xs text-slate-200 focus:border-indigo-500 focus:outline-none"
                  >
                    <option value="">個人私人題庫</option>
                    {classrooms.map((c) => (
                      <option key={c.id} value={String(c.id)}>
                        {c.code} · {c.name}
                      </option>
                    ))}
                  </select>
                </div>

                <div>
                  <label className="mb-1 block text-xs font-medium text-slate-400">指定題號 (選填)</label>
                  <input
                    type="text"
                    value={problemCode}
                    onChange={(e) => setProblemCode(e.target.value)}
                    placeholder="留空自動編號 (如 P1201)"
                    className="w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-xs text-slate-200 placeholder-slate-600 focus:border-indigo-500 focus:outline-none"
                  />
                </div>

                <div>
                  <label className="mb-1 block text-xs font-medium text-slate-400">
                    TeDa 隨機測資: <span className="font-semibold text-indigo-400">{numRandomCases}</span> 筆
                  </label>
                  <input
                    type="range"
                    min={10}
                    max={50}
                    step={5}
                    value={numRandomCases}
                    onChange={(e) => setNumRandomCases(parseInt(e.target.value, 10))}
                    className="w-full accent-indigo-500 mt-2"
                  />
                </div>
              </div>

              {/* Toggles */}
              <div className="grid grid-cols-1 gap-2 rounded-xl border border-slate-800 bg-slate-950/60 p-3 sm:grid-cols-3 text-xs">
                <label className="flex items-center gap-2 text-slate-300 cursor-pointer">
                  <input
                    type="checkbox"
                    checked={autoImport}
                    onChange={(e) => setAutoImport(e.target.checked)}
                    className="h-4 w-4 rounded border-slate-700 bg-slate-800 text-indigo-500 focus:ring-0"
                  />
                  <span>直接匯入系統題庫</span>
                </label>
                <label className="flex items-center gap-2 text-slate-300 cursor-pointer">
                  <input
                    type="checkbox"
                    checked={isExam}
                    onChange={(e) => setIsExam(e.target.checked)}
                    className="h-4 w-4 rounded border-slate-700 bg-slate-800 text-indigo-500 focus:ring-0"
                  />
                  <span>設定為考試題目模式</span>
                </label>
                <label className="flex items-center gap-2 text-slate-300 cursor-pointer">
                  <input
                    type="checkbox"
                    checked={examLockRequired}
                    onChange={(e) => setExamLockRequired(e.target.checked)}
                    className="h-4 w-4 rounded border-slate-700 bg-slate-800 text-indigo-500 focus:ring-0"
                  />
                  <span>強制 Windows 代理鎖定</span>
                </label>
              </div>

              {/* Error banner */}
              {error && (
                <div className="flex items-center gap-2 rounded-xl border border-rose-500/30 bg-rose-500/10 p-3 text-xs text-rose-300">
                  <AlertTriangle className="h-4 w-4 shrink-0" />
                  <span>{error}</span>
                </div>
              )}

              {/* Action buttons */}
              <div className="flex items-center justify-end gap-3 pt-2">
                <button
                  type="button"
                  onClick={onClose}
                  className={buttonClass('subtle', 'text-xs')}
                >
                  取消
                </button>
                <button
                  type="submit"
                  disabled={loading || !prompt.trim()}
                  className={buttonClass('primary', 'text-xs gap-2')}
                >
                  {loading ? (
                    <>
                      <Loader2 className="h-4 w-4 animate-spin" />
                      <span>正在由 Qwen3.8 自動生成與驗證中...</span>
                    </>
                  ) : (
                    <>
                      <Wand2 className="h-4 w-4" />
                      <span>開始 AI 智慧出題</span>
                    </>
                  )}
                </button>
              </div>
            </form>
          ) : (
            /* Result View */
            <div className="space-y-4">
              {/* Success Banner */}
              <div className="rounded-xl border border-emerald-500/30 bg-emerald-500/10 p-4">
                <div className="flex items-start justify-between">
                  <div className="flex items-center gap-3">
                    <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-emerald-500/20 text-emerald-400">
                      <CheckCircle2 className="h-6 w-6" />
                    </div>
                    <div>
                      <div className="flex items-center gap-2">
                        <span className="font-mono font-bold text-emerald-400">
                          {result.problem_code}
                        </span>
                        <h3 className="text-base font-semibold text-slate-100">{result.title}</h3>
                        <span className="rounded-md bg-slate-800 px-2 py-0.5 text-xs font-medium text-slate-300 uppercase">
                          {result.difficulty}
                        </span>
                        {result.dual_run_verified ? (
                          <span className="rounded-md bg-emerald-500/20 px-2 py-0.5 text-xs font-medium text-emerald-300 border border-emerald-500/30">
                            雙軌評測 100% 驗證通過
                          </span>
                        ) : (
                          <span className="rounded-md bg-amber-500/20 px-2 py-0.5 text-xs font-medium text-amber-300 border border-amber-500/30">
                            需手動複核
                          </span>
                        )}
                      </div>
                      <p className="mt-1 text-xs text-slate-400">
                        已產出 {result.sample_cases.length} 組範例測資、{result.hidden_case_count} 筆隨機隱藏測資，檔案已存入{' '}
                        <code className="text-slate-300">{result.folder_path}</code>
                      </p>
                    </div>
                  </div>

                  <div className="flex items-center gap-2">
                    {result.zip_download_url && (
                      <a
                        href={result.zip_download_url}
                        download
                        className={buttonClass('subtle', 'text-xs gap-1.5')}
                      >
                        <Download className="h-3.5 w-3.5" />
                        下載 ZIP 題目包
                      </a>
                    )}
                    {result.problem_id && onProblemCreated && (
                      <button
                        type="button"
                        onClick={() => {
                          onProblemCreated(result.problem_id!)
                          onClose()
                        }}
                        className={buttonClass('primary', 'text-xs gap-1.5')}
                      >
                        <Play className="h-3.5 w-3.5" />
                        立即進入解題
                      </button>
                    )}
                  </div>
                </div>
              </div>

              {/* Tabs */}
              <div className="flex border-b border-slate-800 gap-1 text-xs font-medium">
                <button
                  type="button"
                  onClick={() => setActiveTab('statement')}
                  className={`flex items-center gap-1.5 border-b-2 px-3 py-2 ${
                    activeTab === 'statement'
                      ? 'border-indigo-500 text-indigo-400'
                      : 'border-transparent text-slate-400 hover:text-slate-200'
                  }`}
                >
                  <FileText className="h-3.5 w-3.5" />
                  題本文檔
                </button>
                <button
                  type="button"
                  onClick={() => setActiveTab('ref_code')}
                  className={`flex items-center gap-1.5 border-b-2 px-3 py-2 ${
                    activeTab === 'ref_code'
                      ? 'border-indigo-500 text-indigo-400'
                      : 'border-transparent text-slate-400 hover:text-slate-200'
                  }`}
                >
                  <Code2 className="h-3.5 w-3.5" />
                  C++ 標竿正解
                </button>
                <button
                  type="button"
                  onClick={() => setActiveTab('teda')}
                  className={`flex items-center gap-1.5 border-b-2 px-3 py-2 ${
                    activeTab === 'teda'
                      ? 'border-indigo-500 text-indigo-400'
                      : 'border-transparent text-slate-400 hover:text-slate-200'
                  }`}
                >
                  <Layers className="h-3.5 w-3.5" />
                  TeDa 隨機測資
                </button>
                <button
                  type="button"
                  onClick={() => setActiveTab('rules')}
                  className={`flex items-center gap-1.5 border-b-2 px-3 py-2 ${
                    activeTab === 'rules'
                      ? 'border-indigo-500 text-indigo-400'
                      : 'border-transparent text-slate-400 hover:text-slate-200'
                  }`}
                >
                  <FileCheck className="h-3.5 w-3.5" />
                  語量檢查規則
                </button>
                <button
                  type="button"
                  onClick={() => setActiveTab('samples')}
                  className={`flex items-center gap-1.5 border-b-2 px-3 py-2 ${
                    activeTab === 'samples'
                      ? 'border-indigo-500 text-indigo-400'
                      : 'border-transparent text-slate-400 hover:text-slate-200'
                  }`}
                >
                  <FileCode2 className="h-3.5 w-3.5" />
                  範例測資 ({result.sample_cases.length})
                </button>
              </div>

              {/* Tab Content Box */}
              <div className="rounded-xl border border-slate-800 bg-slate-950 p-4 text-xs font-mono">
                {activeTab === 'statement' && (
                  <div className="space-y-3 font-sans text-slate-300">
                    <div>
                      <h4 className="font-semibold text-slate-100">題目敘述</h4>
                      <p className="mt-1 whitespace-pre-wrap text-sm text-slate-300">
                        {result.description}
                      </p>
                    </div>
                    <div className="grid grid-cols-2 gap-3 pt-2">
                      <div className="rounded-lg bg-slate-900 p-3">
                        <span className="font-semibold text-rose-400">輸入說明</span>
                        <p className="mt-1 whitespace-pre-wrap text-xs text-slate-300">
                          {result.verification_details?.input_format as string || '讀至 EOF'}
                        </p>
                      </div>
                      <div className="rounded-lg bg-slate-900 p-3">
                        <span className="font-semibold text-rose-400">輸出說明</span>
                        <p className="mt-1 whitespace-pre-wrap text-xs text-slate-300">
                          {result.verification_details?.output_format as string || '依題意輸出'}
                        </p>
                      </div>
                    </div>
                  </div>
                )}

                {activeTab === 'ref_code' && (
                  <pre className="max-h-72 overflow-y-auto text-emerald-300">
                    {result.ref_code}
                  </pre>
                )}

                {activeTab === 'teda' && (
                  <pre className="max-h-72 overflow-y-auto text-amber-300">
                    {result.teda_random_script}
                  </pre>
                )}

                {activeTab === 'rules' && (
                  <pre className="max-h-72 overflow-y-auto text-cyan-300">
                    {result.wording_rules}
                  </pre>
                )}

                {activeTab === 'samples' && (
                  <div className="space-y-3 font-mono">
                    {result.sample_cases.map((sc, i) => (
                      <div key={i} className="rounded-lg bg-slate-900 p-2.5">
                        <div className="text-slate-400 font-bold mb-1">範例 #{i + 1}</div>
                        <div className="grid grid-cols-2 gap-2">
                          <div>
                            <span className="text-slate-500">輸入:</span>
                            <pre className="bg-slate-950 p-1.5 rounded text-slate-200 mt-0.5">
                              {sc.input}
                            </pre>
                          </div>
                          <div>
                            <span className="text-slate-500">輸出:</span>
                            <pre className="bg-slate-950 p-1.5 rounded text-emerald-400 mt-0.5">
                              {sc.output}
                            </pre>
                          </div>
                        </div>
                      </div>
                    ))}
                  </div>
                )}
              </div>

              {/* Reset button */}
              <div className="flex justify-end pt-2">
                <button
                  type="button"
                  onClick={() => setResult(null)}
                  className={buttonClass('subtle', 'text-xs')}
                >
                  再次產生新題目
                </button>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
