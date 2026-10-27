import { useState } from 'react'
import {
  AlertCircle,
  Archive,
  CheckCircle2,
  FolderOpen,
  Sparkles,
  Upload,
  X,
} from 'lucide-react'

import { api } from '../api'
import { buttonClass } from '../format'
import { ErrorNotice, Spinner } from './ui'
import type { Classroom, ProblemImportResult } from '../types'

export interface ImportPackModalProps {
  open: boolean
  onClose: () => void
  classrooms: Classroom[]
  defaultClassroomId?: number | null
  onImported: (result: ProblemImportResult) => void
}

type Mode = 'file' | 'path'

export default function ImportPackModal({
  open,
  onClose,
  classrooms,
  defaultClassroomId = null,
  onImported,
}: ImportPackModalProps) {
  const [mode, setMode] = useState<Mode>('file')
  const [file, setFile] = useState<File | null>(null)
  const [directoryPath, setDirectoryPath] = useState('')
  const [classroomId, setClassroomId] = useState<string>(
    defaultClassroomId ? String(defaultClassroomId) : '',
  )
  const [generateHidden, setGenerateHidden] = useState(true)
  const [numRandom, setNumRandom] = useState(30)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [importResult, setImportResult] = useState<ProblemImportResult | null>(null)

  if (!open) return null

  const handleClose = () => {
    if (busy) return
    setError(null)
    setImportResult(null)
    onClose()
  }

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    setError(null)
    setBusy(true)

    try {
      let result: ProblemImportResult
      const cid = classroomId ? Number(classroomId) : null

      if (mode === 'file') {
        if (!file) {
          setError('請選擇要上傳的 .zip 題庫檔案')
          setBusy(false)
          return
        }
        const formData = new FormData()
        formData.append('file', file)
        if (cid !== null) formData.append('classroom_id', String(cid))
        formData.append('generate_hidden', String(generateHidden))
        formData.append('num_random', String(numRandom))
        result = await api.importPackFile(formData)
      } else {
        if (!directoryPath.trim()) {
          setError('請輸入伺服器端題庫資料夾完整路徑')
          setBusy(false)
          return
        }
        result = await api.importPack({
          directory_path: directoryPath.trim(),
          classroom_id: cid,
          generate_hidden: generateHidden,
          num_random: numRandom,
        })
      }

      setImportResult(result)
      onImported(result)
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm p-4 overflow-y-auto">
      <div className="relative w-full max-w-xl rounded-2xl bg-slate-900 border border-slate-700/80 shadow-2xl p-6 text-slate-200">
        <button
          type="button"
          onClick={handleClose}
          disabled={busy}
          className="absolute top-4 right-4 p-1.5 text-slate-400 hover:text-white rounded-lg hover:bg-slate-800 transition"
        >
          <X className="w-5 h-5" />
        </button>

        <div className="flex items-center gap-2.5 mb-5">
          <div className="p-2.5 rounded-xl bg-indigo-500/10 text-indigo-400 border border-indigo-500/20">
            <Archive className="w-5 h-5" />
          </div>
          <div>
            <h2 className="text-lg font-bold text-white tracking-tight">
              匯入庫題卡 (Problem Pack)
            </h2>
            <p className="text-xs text-slate-400">
              支援自動發現出題規範、範例卡、TeDa 隨機測資腳本與標碼
            </p>
          </div>
        </div>

        {importResult ? (
          <div className="space-y-4">
            <div className="p-4 rounded-xl bg-emerald-500/10 border border-emerald-500/30 text-emerald-300">
              <div className="flex items-center gap-2 font-semibold text-emerald-200 mb-1">
                <CheckCircle2 className="w-5 h-5 text-emerald-400" />
                <span>庫題卡匯入成功！</span>
              </div>
              <p className="text-xs text-emerald-300/90">
                已成功建立題庫題目：
                <strong className="text-white ml-1 font-mono">
                  {importResult.problem_code || importResult.title}
                </strong>
              </p>
            </div>

            <div className="grid grid-cols-2 gap-3 text-xs bg-slate-950/60 p-4 rounded-xl border border-slate-800">
              <div>
                <span className="text-slate-400">題號：</span>
                <span className="text-white font-mono ml-1">{importResult.problem_code || '-'}</span>
              </div>
              <div>
                <span className="text-slate-400">難度：</span>
                <span className="text-white capitalize ml-1">{importResult.difficulty}</span>
              </div>
              <div>
                <span className="text-slate-400">公開範例測資：</span>
                <span className="text-emerald-400 font-mono ml-1">
                  {importResult.sample_case_count} 筆
                </span>
              </div>
              <div>
                <span className="text-slate-400">TeDa 隨機測資：</span>
                <span className="text-indigo-400 font-mono ml-1">
                  {importResult.random_case_count} 筆
                </span>
              </div>
              <div>
                <span className="text-slate-400">限時 / 記憶體：</span>
                <span className="text-white ml-1">
                  {importResult.time_limit_sec}s / {importResult.memory_limit_mb}MB
                </span>
              </div>
              <div>
                <span className="text-slate-400">標碼參考解答：</span>
                <span className="text-white ml-1">
                  {importResult.has_ref_code ? '✓ 已內嵌 (伺服端保密)' : '無'}
                </span>
              </div>
              <div>
                <span className="text-slate-400">限碼詞規則：</span>
                <span className="text-white ml-1">
                  {importResult.has_wording_rules ? '✓ TeDa-Wording 啟用' : '無'}
                </span>
              </div>
              <div>
                <span className="text-slate-400">題目 PDF 題目卡：</span>
                <span className="text-white ml-1">
                  {importResult.has_statement_pdf ? '✓ 已保存' : '無'}
                </span>
              </div>
            </div>

            {importResult.warnings && importResult.warnings.length > 0 ? (
              <div className="p-3 rounded-lg bg-amber-500/10 border border-amber-500/20 text-xs text-amber-300 space-y-1">
                <div className="flex items-center gap-1.5 font-medium text-amber-200">
                  <AlertCircle className="w-4 h-4 text-amber-400" />
                  <span>匯入提示：</span>
                </div>
                <ul className="list-disc pl-5 space-y-0.5">
                  {importResult.warnings.map((w, idx) => (
                    <li key={idx}>{w}</li>
                  ))}
                </ul>
              </div>
            ) : null}

            <div className="flex justify-end pt-2">
              <button
                type="button"
                onClick={handleClose}
                className={buttonClass('primary', 'text-xs')}
              >
                完成
              </button>
            </div>
          </div>
        ) : (
          <form onSubmit={handleSubmit} className="space-y-4 text-xs">
            {error ? <ErrorNotice message={error} /> : null}

            {/* Mode switch */}
            <div className="flex rounded-lg bg-slate-950/80 p-1 border border-slate-800">
              <button
                type="button"
                onClick={() => setMode('file')}
                className={`flex-1 py-1.5 rounded-md flex items-center justify-center gap-1.5 font-medium transition ${
                  mode === 'file'
                    ? 'bg-indigo-600 text-white shadow'
                    : 'text-slate-400 hover:text-slate-200'
                }`}
              >
                <Upload className="w-3.5 h-3.5" />
                <span>上傳 ZIP 封裝包</span>
              </button>
              <button
                type="button"
                onClick={() => setMode('path')}
                className={`flex-1 py-1.5 rounded-md flex items-center justify-center gap-1.5 font-medium transition ${
                  mode === 'path'
                    ? 'bg-indigo-600 text-white shadow'
                    : 'text-slate-400 hover:text-slate-200'
                }`}
              >
                <FolderOpen className="w-3.5 h-3.5" />
                <span>伺服器目錄路徑</span>
              </button>
            </div>

            {mode === 'file' ? (
              <div>
                <label className="block text-slate-300 font-medium mb-1.5">
                  選擇 ZIP 題目卡檔案 (.zip)
                </label>
                <input
                  type="file"
                  accept=".zip"
                  onChange={(e) => setFile(e.target.files?.[0] || null)}
                  className="block w-full text-xs text-slate-400 file:mr-3 file:py-2 file:px-4 file:rounded-lg file:border-0 file:text-xs file:font-semibold file:bg-indigo-600 file:text-white hover:file:bg-indigo-500 cursor-pointer bg-slate-950/60 rounded-xl border border-slate-800 p-2"
                />
                <p className="mt-1 text-[11px] text-slate-400">
                  可上傳包含庫題卡測資範例、出題設定說明及標碼的 ZIP 封裝包
                </p>
              </div>
            ) : (
              <div>
                <label className="block text-slate-300 font-medium mb-1.5">
                  伺服器端目錄絕對路徑
                </label>
                <input
                  type="text"
                  placeholder="/home/zzlee/vibecoding/codingpass-tests/P1001_反轉單字"
                  value={directoryPath}
                  onChange={(e) => setDirectoryPath(e.target.value)}
                  className="w-full bg-slate-950/80 border border-slate-700 rounded-lg px-3 py-2 text-white font-mono placeholder:text-slate-600 focus:outline-none focus:border-indigo-500"
                />
              </div>
            )}

            {/* Target Classroom */}
            <div>
              <label className="block text-slate-300 font-medium mb-1.5">
                發佈至班級 (若留空則為個人草稿題庫)
              </label>
              <select
                value={classroomId}
                onChange={(e) => setClassroomId(e.target.value)}
                className="w-full bg-slate-950/80 border border-slate-700 rounded-lg px-3 py-2 text-white focus:outline-none focus:border-indigo-500"
              >
                <option value="">個人題庫 (尚未指派給班級)</option>
                {classrooms.map((c) => (
                  <option key={c.id} value={c.id}>
                    {c.name} ({c.code})
                  </option>
                ))}
              </select>
            </div>

            {/* TeDa random options */}
            <div className="p-3 rounded-xl bg-slate-950/60 border border-slate-800/80 space-y-2.5">
              <label className="flex items-center gap-2 cursor-pointer">
                <input
                  type="checkbox"
                  checked={generateHidden}
                  onChange={(e) => setGenerateHidden(e.target.checked)}
                  className="rounded border-slate-700 text-indigo-600 focus:ring-indigo-500 w-4 h-4"
                />
                <span className="font-medium text-slate-200">
                  自動以 TeDa 腳本生成隨機測資 (Dual-Run 標碼產出正確解答)
                </span>
              </label>

              {generateHidden ? (
                <div className="flex items-center gap-3 pl-6">
                  <span className="text-slate-400">隱藏測資筆數：</span>
                  <input
                    type="number"
                    min={1}
                    max={50}
                    value={numRandom}
                    onChange={(e) => setNumRandom(Math.max(1, Math.min(50, Number(e.target.value))))}
                    className="w-20 bg-slate-900 border border-slate-700 rounded-md px-2.5 py-1 text-white text-center font-mono focus:outline-none focus:border-indigo-500"
                  />
                  <span className="text-[11px] text-slate-400">(預設 30 筆，上限 50 筆)</span>
                </div>
              ) : null}
            </div>

            <div className="flex items-center justify-end gap-2.5 pt-3 border-t border-slate-800">
              <button
                type="button"
                onClick={handleClose}
                disabled={busy}
                className={buttonClass('ghost', 'text-xs')}
              >
                取消
              </button>
              <button
                type="submit"
                disabled={busy}
                className={buttonClass('primary', 'text-xs')}
              >
                {busy ? <Spinner /> : <Sparkles className="w-4 h-4" />}
                <span>{busy ? '正在解析並執行 Dual-Run...' : '開始匯入'}</span>
              </button>
            </div>
          </form>
        )}
      </div>
    </div>
  )
}
