/**
 * Tab descriptors for the application header.
 *
 * A plain module (rather than an export of `Header.tsx`) so header-only concerns
 * stay there and the tab list can be reused - and hot reloaded - independently.
 */
import { BookOpen, ChartColumn, School, SquareTerminal, type LucideIcon } from 'lucide-react'

import type { AppTab } from './types'

export interface TabDescriptor {
  id: AppTab
  /** Emoji kept in the label: it makes the tab strip scannable on a projector. */
  emoji: string
  label: string
  Icon: LucideIcon
  hint: string
}

/** Order here is the order of the tabs in the header. */
export const APP_TABS: readonly TabDescriptor[] = [
  {
    id: 'playground',
    emoji: '💻',
    label: 'Playground',
    Icon: SquareTerminal,
    hint: 'Free-form C / C++ editor with stdin and stdout',
  },
  {
    id: 'problems',
    emoji: '📚',
    label: 'Problem Bank',
    Icon: BookOpen,
    hint: 'Graded exercises with hidden test cases',
  },
  {
    id: 'classrooms',
    emoji: '🏫',
    label: 'Classrooms',
    Icon: School,
    hint: 'Classes, invite codes and rosters',
  },
  {
    id: 'submissions',
    emoji: '📊',
    label: 'Submissions',
    Icon: ChartColumn,
    hint: 'Recent verdicts and scores',
  },
]

export default APP_TABS
