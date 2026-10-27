/** Consistent heading row used by every panel below the app header. */
import type { ReactNode } from 'react'

export interface SectionHeadingProps {
  title: string
  hint?: string
  icon?: ReactNode
  actions?: ReactNode
}

export function SectionHeading({ title, hint, icon, actions }: SectionHeadingProps) {
  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-2 border-b border-slate-800 px-4 py-3">
      {icon ? <span className="text-sky-400">{icon}</span> : null}
      <div className="min-w-0">
        <h2 className="truncate text-sm font-semibold text-white">{title}</h2>
        {hint ? <p className="text-[11px] text-slate-500">{hint}</p> : null}
      </div>
      {actions ? <div className="ml-auto flex flex-wrap items-center gap-2">{actions}</div> : null}
    </div>
  )
}

export default SectionHeading
