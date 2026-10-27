/**
 * Change password modal for self-service credential updates.
 */
import { useState, type FormEvent } from 'react'
import { CircleAlert, CircleCheck, KeyRound, Lock } from 'lucide-react'

import { Field, Modal, Spinner } from './ui'
import { api, ApiError } from '../api'
import { INPUT_CLASS, buttonClass } from '../format'

export interface ChangePasswordModalProps {
  open: boolean
  onClose: () => void
}

export function ChangePasswordModal({ open, onClose }: ChangePasswordModalProps) {
  const [oldPassword, setOldPassword] = useState('')
  const [newPassword, setNewPassword] = useState('')
  const [confirmPassword, setConfirmPassword] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [success, setSuccess] = useState<string | null>(null)

  const handleClose = () => {
    if (busy) return
    setOldPassword('')
    setNewPassword('')
    setConfirmPassword('')
    setError(null)
    setSuccess(null)
    onClose()
  }

  const handleSubmit = async (event: FormEvent) => {
    event.preventDefault()
    if (busy) return
    setError(null)
    setSuccess(null)

    if (!oldPassword) {
      setError('請輸入目前的密碼 (Current password is required).')
      return
    }
    if (newPassword.length < 6) {
      setError('新密碼長度至少需 6 個字元 (New password must be at least 6 characters).')
      return
    }
    if (newPassword !== confirmPassword) {
      setError('兩次輸入的新密碼不一致 (New passwords do not match).')
      return
    }

    setBusy(true)
    try {
      const resp = await api.changePassword({
        old_password: oldPassword,
        new_password: newPassword,
      })
      setSuccess(resp.message || '密碼變更成功！')
      setTimeout(() => {
        handleClose()
      }, 1200)
    } catch (err) {
      if (err instanceof ApiError) {
        setError(err.detail)
      } else {
        setError(err instanceof Error ? err.message : String(err))
      }
    } finally {
      setBusy(false)
    }
  }

  return (
    <Modal
      open={open}
      onClose={handleClose}
      title="變更密碼 (Change Password)"
      subtitle="更新您目前帳號的登入密碼"
      icon={<KeyRound className="h-5 w-5 text-sky-400" aria-hidden="true" />}
      footer={
        <>
          <button
            type="button"
            onClick={handleClose}
            disabled={busy}
            className={buttonClass('subtle')}
          >
            取消
          </button>
          <button
            type="submit"
            form="change-password-form"
            disabled={busy || !!success}
            className={buttonClass('primary')}
          >
            {busy ? (
              <>
                <Spinner />
                <span>儲存中…</span>
              </>
            ) : (
              <>
                <Lock className="h-3.5 w-3.5" aria-hidden="true" />
                <span>確認變更</span>
              </>
            )}
          </button>
        </>
      }
    >
      <form id="change-password-form" onSubmit={handleSubmit} className="space-y-4">
        {error ? (
          <div
            role="alert"
            className="flex items-start gap-2.5 rounded-lg border border-rose-500/30 bg-rose-500/10 p-3 text-xs text-rose-300"
          >
            <CircleAlert className="h-4 w-4 shrink-0 text-rose-400" aria-hidden="true" />
            <span className="leading-snug">{error}</span>
          </div>
        ) : null}

        {success ? (
          <div
            role="status"
            className="flex items-start gap-2.5 rounded-lg border border-emerald-500/30 bg-emerald-500/10 p-3 text-xs text-emerald-300"
          >
            <CircleCheck className="h-4 w-4 shrink-0 text-emerald-400" aria-hidden="true" />
            <span className="leading-snug">{success}</span>
          </div>
        ) : null}

        <Field label="目前密碼 (Current Password)">
          <input
            type="password"
            autoComplete="current-password"
            value={oldPassword}
            onChange={(e) => setOldPassword(e.target.value)}
            disabled={busy || !!success}
            placeholder="請輸入現有密碼"
            className={INPUT_CLASS}
            required
          />
        </Field>

        <Field label="新密碼 (New Password)" hint="至少 6 個字元">
          <input
            type="password"
            autoComplete="new-password"
            value={newPassword}
            onChange={(e) => setNewPassword(e.target.value)}
            disabled={busy || !!success}
            placeholder="請輸入新密碼 (至少 6 碼)"
            className={INPUT_CLASS}
            required
            minLength={6}
          />
        </Field>

        <Field label="確認新密碼 (Confirm New Password)">
          <input
            type="password"
            autoComplete="new-password"
            value={confirmPassword}
            onChange={(e) => setConfirmPassword(e.target.value)}
            disabled={busy || !!success}
            placeholder="請再次輸入新密碼"
            className={INPUT_CLASS}
            required
            minLength={6}
          />
        </Field>
      </form>
    </Modal>
  )
}

export default ChangePasswordModal
