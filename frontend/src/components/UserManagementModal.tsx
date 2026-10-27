/**
 * Admin User Management Modal:
 * List users, filter by role, search, promote/demote roles, reset passwords,
 * register new teacher/admin/student accounts, and delete accounts.
 */
import { useEffect, useState, useCallback, type FormEvent } from 'react'
import {
  CircleAlert,
  CircleCheck,
  KeyRound,
  Plus,
  RefreshCw,
  Search,
  Trash2,
  UserCheck,
  UserPlus,
  Users,
} from 'lucide-react'

import { Field, Modal, RoleBadge, Spinner } from './ui'
import { api, ApiError } from '../api'
import { useAuth } from '../authContext'
import { INPUT_CLASS, buttonClass, formatTimestamp } from '../format'
import type { Role, User } from '../types'

const ROLE_OPTIONS: { value: Role | ''; label: string }[] = [
  { value: '', label: '所有身分 (All Roles)' },
  { value: 'admin', label: '管理員 (Admin)' },
  { value: 'teacher', label: '教師 (Teacher)' },
  { value: 'student', label: '學生 (Student)' },
]

export interface UserManagementModalProps {
  open: boolean
  onClose: () => void
}

export function UserManagementModal({ open, onClose }: UserManagementModalProps) {
  const { user: currentUser } = useAuth()
  const [users, setUsers] = useState<User[]>([])
  const [total, setTotal] = useState(0)
  const [loading, setLoading] = useState(false)
  const [selectedRole, setSelectedRole] = useState<Role | ''>('')
  const [searchQuery, setSearchQuery] = useState('')
  const [debouncedSearch, setDebouncedSearch] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [feedback, setFeedback] = useState<string | null>(null)

  // Sub-modal states
  const [showCreateForm, setShowCreateForm] = useState(false)
  const [resetTargetUser, setResetTargetUser] = useState<User | null>(null)
  const [newPasswordInput, setNewPasswordInput] = useState('')
  const [deleteTargetUser, setDeleteTargetUser] = useState<User | null>(null)
  const [actionBusy, setActionBusy] = useState(false)

  // Create form fields
  const [newUsername, setNewUsername] = useState('')
  const [newEmail, setNewEmail] = useState('')
  const [newFullName, setNewFullName] = useState('')
  const [newRole, setNewRole] = useState<Role>('teacher')
  const [newPassword, setNewPassword] = useState('')

  // Debounce search input
  useEffect(() => {
    const timer = setTimeout(() => {
      setDebouncedSearch(searchQuery)
    }, 300)
    return () => clearTimeout(timer)
  }, [searchQuery])

  const fetchUsers = useCallback(async () => {
    if (!open) return
    setLoading(true)
    setError(null)
    try {
      const res = await api.listUsers({
        role: selectedRole || undefined,
        search: debouncedSearch.trim() || undefined,
        limit: 100,
      })
      setUsers(res.users)
      setTotal(res.total)
    } catch (err) {
      if (err instanceof ApiError) {
        setError(err.detail)
      } else {
        setError(err instanceof Error ? err.message : String(err))
      }
    } finally {
      setLoading(false)
    }
  }, [open, selectedRole, debouncedSearch])

  useEffect(() => {
    if (open) {
      fetchUsers()
    }
  }, [open, fetchUsers])

  const handleRoleChange = async (targetUser: User, nextRole: Role) => {
    if (targetUser.role === nextRole || actionBusy) return
    setActionBusy(true)
    setError(null)
    setFeedback(null)
    try {
      const updated = await api.updateUser(targetUser.id, { role: nextRole })
      setFeedback(`已將 ${updated.username} 的身分變更為 ${nextRole}。`)
      setUsers((prev) => prev.map((u) => (u.id === updated.id ? updated : u)))
    } catch (err) {
      if (err instanceof ApiError) {
        setError(err.detail)
      } else {
        setError(err instanceof Error ? err.message : String(err))
      }
    } finally {
      setActionBusy(false)
    }
  }

  const handleAdminResetPassword = async (event: FormEvent) => {
    event.preventDefault()
    if (!resetTargetUser || !newPasswordInput || actionBusy) return
    if (newPasswordInput.length < 6) {
      setError('新密碼長度至少需 6 個字元。')
      return
    }

    setActionBusy(true)
    setError(null)
    setFeedback(null)
    try {
      await api.updateUser(resetTargetUser.id, { password: newPasswordInput })
      setFeedback(`已成功為 ${resetTargetUser.username} 重設密碼。`)
      setResetTargetUser(null)
      setNewPasswordInput('')
    } catch (err) {
      if (err instanceof ApiError) {
        setError(err.detail)
      } else {
        setError(err instanceof Error ? err.message : String(err))
      }
    } finally {
      setActionBusy(false)
    }
  }

  const handleDeleteUser = async () => {
    if (!deleteTargetUser || actionBusy) return
    setActionBusy(true)
    setError(null)
    setFeedback(null)
    try {
      const res = await api.deleteUser(deleteTargetUser.id)
      setFeedback(res.message || `帳號 ${deleteTargetUser.username} 已刪除。`)
      setDeleteTargetUser(null)
      fetchUsers()
    } catch (err) {
      if (err instanceof ApiError) {
        setError(err.detail)
      } else {
        setError(err instanceof Error ? err.message : String(err))
      }
    } finally {
      setActionBusy(false)
    }
  }

  const handleCreateUser = async (event: FormEvent) => {
    event.preventDefault()
    if (actionBusy) return
    if (newPassword.length < 6) {
      setError('密碼長度至少需 6 個字元。')
      return
    }

    setActionBusy(true)
    setError(null)
    setFeedback(null)
    try {
      const created = await api.register({
        username: newUsername.trim(),
        email: newEmail.trim(),
        full_name: newFullName.trim() || undefined,
        password: newPassword,
        role: newRole,
      })
      setFeedback(`已成功建立 ${created.role} 帳號：${created.username}。`)
      setShowCreateForm(false)
      setNewUsername('')
      setNewEmail('')
      setNewFullName('')
      setNewPassword('')
      fetchUsers()
    } catch (err) {
      if (err instanceof ApiError) {
        setError(err.detail)
      } else {
        setError(err instanceof Error ? err.message : String(err))
      }
    } finally {
      setActionBusy(false)
    }
  }

  return (
    <>
      <Modal
        open={open}
        onClose={onClose}
        title="使用者帳號管理 (User Management)"
        subtitle="檢視、管理系統使用者帳號、權限身分與密碼重設"
        icon={<Users className="h-5 w-5 text-sky-400" aria-hidden="true" />}
        wide
        footer={
          <div className="flex w-full items-center justify-between">
            <span className="text-xs text-slate-400">
              共計 <strong className="text-slate-200">{total}</strong> 個帳號
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
          {/* Alerts */}
          {error ? (
            <div
              role="alert"
              className="flex items-start gap-2.5 rounded-lg border border-rose-500/30 bg-rose-500/10 p-3 text-xs text-rose-300"
            >
              <CircleAlert className="h-4 w-4 shrink-0 text-rose-400" aria-hidden="true" />
              <span className="leading-snug">{error}</span>
            </div>
          ) : null}

          {feedback ? (
            <div
              role="status"
              className="flex items-start gap-2.5 rounded-lg border border-emerald-500/30 bg-emerald-500/10 p-3 text-xs text-emerald-300"
            >
              <CircleCheck className="h-4 w-4 shrink-0 text-emerald-400" aria-hidden="true" />
              <span className="leading-snug">{feedback}</span>
            </div>
          ) : null}

          {/* Action Toolbar */}
          <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
            <div className="flex flex-1 flex-wrap items-center gap-2">
              {/* Search Bar */}
              <div className="relative flex-1 min-w-[200px]">
                <Search className="absolute left-3 top-2.5 h-4 w-4 text-slate-500" aria-hidden="true" />
                <input
                  type="text"
                  placeholder="搜尋帳號、姓名、Email…"
                  value={searchQuery}
                  onChange={(e) => setSearchQuery(e.target.value)}
                  className={`${INPUT_CLASS} pl-9 text-xs`}
                />
              </div>

              {/* Role Filter */}
              <select
                value={selectedRole}
                onChange={(e) => setSelectedRole(e.target.value as Role | '')}
                className={`${INPUT_CLASS} w-auto text-xs`}
                aria-label="Filter users by role"
              >
                {ROLE_OPTIONS.map((opt) => (
                  <option key={opt.value} value={opt.value}>
                    {opt.label}
                  </option>
                ))}
              </select>

              {/* Refresh button */}
              <button
                type="button"
                onClick={fetchUsers}
                disabled={loading}
                title="重新整理使用者清單"
                className="rounded-lg border border-slate-700 bg-slate-800 p-2 text-slate-400 hover:text-slate-200 transition-colors"
              >
                <RefreshCw className={`h-4 w-4 ${loading ? 'animate-spin' : ''}`} aria-hidden="true" />
              </button>
            </div>

            {/* Create User Button */}
            <button
              type="button"
              onClick={() => setShowCreateForm((prev) => !prev)}
              className={buttonClass('primary')}
            >
              <UserPlus className="h-4 w-4" aria-hidden="true" />
              <span>{showCreateForm ? '收合新增表單' : '新增帳號'}</span>
            </button>
          </div>

          {/* Collapsible Create User Form */}
          {showCreateForm ? (
            <div className="rounded-xl border border-sky-500/30 bg-sky-950/20 p-4">
              <h4 className="flex items-center gap-2 text-sm font-semibold text-sky-200 mb-3">
                <Plus className="h-4 w-4" aria-hidden="true" />
                新增使用者 / 指派身分
              </h4>
              <form onSubmit={handleCreateUser} className="space-y-3">
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                  <Field label="使用者帳號 (Username)">
                    <input
                      type="text"
                      required
                      placeholder="e.g. prof_lin"
                      value={newUsername}
                      onChange={(e) => setNewUsername(e.target.value)}
                      className={INPUT_CLASS}
                    />
                  </Field>
                  <Field label="電子信箱 (Email)">
                    <input
                      type="email"
                      required
                      placeholder="e.g. lin@univ.edu"
                      value={newEmail}
                      onChange={(e) => setNewEmail(e.target.value)}
                      className={INPUT_CLASS}
                    />
                  </Field>
                  <Field label="真實姓名 (Full Name)">
                    <input
                      type="text"
                      placeholder="e.g. 林教授"
                      value={newFullName}
                      onChange={(e) => setNewFullName(e.target.value)}
                      className={INPUT_CLASS}
                    />
                  </Field>
                  <Field label="帳號身分 (Role)">
                    <select
                      value={newRole}
                      onChange={(e) => setNewRole(e.target.value as Role)}
                      className={INPUT_CLASS}
                    >
                      <option value="teacher">教師 (Teacher)</option>
                      <option value="admin">管理員 (Admin)</option>
                      <option value="student">學生 (Student)</option>
                    </select>
                  </Field>
                </div>
                <Field label="初始密碼 (Initial Password)" hint="至少 6 個字元">
                  <input
                    type="password"
                    required
                    minLength={6}
                    placeholder="請設定登入密碼"
                    value={newPassword}
                    onChange={(e) => setNewPassword(e.target.value)}
                    className={INPUT_CLASS}
                  />
                </Field>
                <div className="flex justify-end gap-2 pt-2">
                  <button
                    type="button"
                    onClick={() => setShowCreateForm(false)}
                    className={buttonClass('subtle')}
                  >
                    取消
                  </button>
                  <button
                    type="submit"
                    disabled={actionBusy}
                    className={buttonClass('primary')}
                  >
                    {actionBusy ? <Spinner /> : <UserCheck className="h-4 w-4" aria-hidden="true" />}
                    <span>建立帳號</span>
                  </button>
                </div>
              </form>
            </div>
          ) : null}

          {/* User Table */}
          <div className="overflow-x-auto rounded-xl border border-slate-800 bg-slate-950/40">
            <table className="w-full text-left text-xs">
              <thead className="border-b border-slate-800 bg-slate-900/60 font-semibold text-slate-400">
                <tr>
                  <th className="px-3.5 py-2.5">ID</th>
                  <th className="px-3.5 py-2.5">帳號 / 姓名</th>
                  <th className="px-3.5 py-2.5">電子信箱</th>
                  <th className="px-3.5 py-2.5">身分權限</th>
                  <th className="px-3.5 py-2.5">註冊時間</th>
                  <th className="px-3.5 py-2.5 text-right">操作</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-800/60 font-sans">
                {users.length === 0 ? (
                  <tr>
                    <td colSpan={6} className="px-4 py-8 text-center text-slate-500">
                      {loading ? '載入使用者中…' : '未找到符合條件的使用者帳號。'}
                    </td>
                  </tr>
                ) : (
                  users.map((u) => {
                    const isSelf = currentUser?.id === u.id
                    return (
                      <tr key={u.id} className="hover:bg-slate-800/30 transition-colors">
                        <td className="px-3.5 py-2.5 font-mono text-slate-500">#{u.id}</td>
                        <td className="px-3.5 py-2.5">
                          <div className="flex items-center gap-2">
                            <span className="font-semibold text-slate-200">{u.username}</span>
                            {isSelf ? (
                              <span className="rounded bg-sky-500/20 px-1 py-0.5 text-[10px] font-medium text-sky-300">
                                目前帳號
                              </span>
                            ) : null}
                          </div>
                          {u.full_name ? (
                            <div className="text-slate-400 text-[11px]">{u.full_name}</div>
                          ) : null}
                        </td>
                        <td className="px-3.5 py-2.5 font-mono text-slate-400">{u.email}</td>
                        <td className="px-3.5 py-2.5">
                          <div className="flex items-center gap-2">
                            <RoleBadge role={u.role} />
                            <select
                              aria-label={`Change role for user ${u.username}`}
                              value={u.role}
                              disabled={actionBusy}
                              onChange={(e) => handleRoleChange(u, e.target.value as Role)}
                              className="rounded border border-slate-700 bg-slate-900 px-1.5 py-0.5 text-[11px] text-slate-300 hover:border-slate-600 focus:outline-none"
                            >
                              <option value="student">student</option>
                              <option value="teacher">teacher</option>
                              <option value="admin">admin</option>
                            </select>
                          </div>
                        </td>
                        <td className="px-3.5 py-2.5 text-[11px] text-slate-400">
                          {formatTimestamp(u.created_at)}
                        </td>
                        <td className="px-3.5 py-2.5 text-right">
                          <div className="inline-flex items-center gap-1.5">
                            {/* Reset Password */}
                            <button
                              type="button"
                              onClick={() => {
                                setResetTargetUser(u)
                                setNewPasswordInput('')
                                setError(null)
                              }}
                              title={`重設 ${u.username} 的密碼`}
                              className="inline-flex items-center gap-1 rounded-md border border-slate-700 bg-slate-800/80 px-2 py-1 text-[11px] font-medium text-slate-300 hover:border-sky-500/50 hover:text-sky-200 transition-colors"
                            >
                              <KeyRound className="h-3 w-3" aria-hidden="true" />
                              <span>重設密碼</span>
                            </button>

                            {/* Delete User */}
                            <button
                              type="button"
                              disabled={isSelf || actionBusy}
                              onClick={() => {
                                setDeleteTargetUser(u)
                                setError(null)
                              }}
                              title={isSelf ? '無法刪除您自己的帳號' : `刪除 ${u.username}`}
                              className="inline-flex items-center gap-1 rounded-md border border-slate-700 bg-slate-800/80 px-2 py-1 text-[11px] font-medium text-slate-400 hover:border-rose-500/50 hover:text-rose-200 disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
                            >
                              <Trash2 className="h-3 w-3" aria-hidden="true" />
                              <span>刪除</span>
                            </button>
                          </div>
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

      {/* Admin Reset Password Sub-Modal */}
      {resetTargetUser ? (
        <Modal
          open={!!resetTargetUser}
          onClose={() => setResetTargetUser(null)}
          title={`重設使用者密碼: ${resetTargetUser.username}`}
          subtitle="管理員直接重設使用者密碼，該使用者將以此密碼登入。"
          icon={<KeyRound className="h-5 w-5 text-sky-400" aria-hidden="true" />}
          footer={
            <>
              <button
                type="button"
                onClick={() => setResetTargetUser(null)}
                disabled={actionBusy}
                className={buttonClass('subtle')}
              >
                取消
              </button>
              <button
                type="submit"
                form="admin-reset-pwd-form"
                disabled={actionBusy}
                className={buttonClass('primary')}
              >
                {actionBusy ? <Spinner /> : <KeyRound className="h-3.5 w-3.5" aria-hidden="true" />}
                <span>確定重設密碼</span>
              </button>
            </>
          }
        >
          <form id="admin-reset-pwd-form" onSubmit={handleAdminResetPassword} className="space-y-4">
            <Field label="新設定密碼 (New Password)" hint="至少 6 個字元">
              <input
                type="password"
                required
                minLength={6}
                value={newPasswordInput}
                onChange={(e) => setNewPasswordInput(e.target.value)}
                placeholder="輸入重設之新密碼"
                className={INPUT_CLASS}
                autoFocus
              />
            </Field>
          </form>
        </Modal>
      ) : null}

      {/* Delete User Confirmation Modal */}
      {deleteTargetUser ? (
        <Modal
          open={!!deleteTargetUser}
          onClose={() => setDeleteTargetUser(null)}
          title={`確認刪除帳號: ${deleteTargetUser.username}？`}
          subtitle="此操作將永久刪除該使用者帳號及其所有相關權限關聯。"
          icon={<Trash2 className="h-5 w-5 text-rose-400" aria-hidden="true" />}
          footer={
            <>
              <button
                type="button"
                onClick={() => setDeleteTargetUser(null)}
                disabled={actionBusy}
                className={buttonClass('subtle')}
              >
                取消
              </button>
              <button
                type="button"
                onClick={handleDeleteUser}
                disabled={actionBusy}
                className={buttonClass('danger')}
              >
                {actionBusy ? <Spinner /> : <Trash2 className="h-3.5 w-3.5" aria-hidden="true" />}
                <span>確認永久刪除</span>
              </button>
            </>
          }
        >
          <div className="space-y-2 text-xs text-slate-300">
            <p>
              您確定要刪除帳號 <strong className="text-white">{deleteTargetUser.username}</strong> ({deleteTargetUser.email}) 嗎？
            </p>
            <p className="text-rose-300">
              請注意：刪除後將無法復原，該帳號將無法再登入系統。
            </p>
          </div>
        </Modal>
      ) : null}
    </>
  )
}

export default UserManagementModal
