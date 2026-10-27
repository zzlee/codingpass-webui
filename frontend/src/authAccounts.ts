/**
 * Seeded demo accounts for the one-click role switcher.
 *
 * These match the rows created by `backend/database.py` on first start, which is
 * what makes instant switching possible. They exist for classroom demos on a
 * fresh instance - never point this build at production data with these
 * credentials still seeded.
 */
import type { Role } from './types'

export interface QuickAccount {
  key: string
  /** Name shown in the switcher (`Alice`). */
  label: string
  /** Secondary line (`student1 / student123`). */
  hint: string
  username: string
  password: string
  role: Role
  /** Emoji used to keep the switcher scannable during a lesson. */
  emoji: string
}

export const QUICK_ACCOUNTS: readonly QuickAccount[] = [
  {
    key: 'alice',
    label: 'Alice',
    hint: 'student1 / student123',
    username: 'student1',
    password: 'student123',
    role: 'student',
    emoji: '🎓',
  },
  {
    key: 'bob',
    label: 'Bob',
    hint: 'student2 / student123',
    username: 'student2',
    password: 'student123',
    role: 'student',
    emoji: '🧑‍💻',
  },
  {
    key: 'chen',
    label: 'Prof. Chen',
    hint: 'teacher1 / teacher123',
    username: 'teacher1',
    password: 'teacher123',
    role: 'teacher',
    emoji: '🧑‍🏫',
  },
  {
    key: 'admin',
    label: 'Site Administrator',
    hint: 'admin / admin123',
    username: 'admin',
    password: 'admin123',
    role: 'admin',
    emoji: '🛡️',
  },
]

export default QUICK_ACCOUNTS
