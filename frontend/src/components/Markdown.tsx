/**
 * Dependency-free Markdown renderer for problem statements.
 *
 * Problem descriptions are authored by teachers, so the renderer must be safe:
 * it never emits raw HTML (`dangerouslySetInnerHTML` is not used anywhere) and
 * builds React elements instead, which escapes every piece of user text. Link
 * targets are additionally restricted to `http(s):`, `mailto:` and same-origin
 * paths, so `[click me](javascript:...)` is inert.
 *
 * Supported syntax covers what a judge statement actually needs: ATX headings,
 * paragraphs, fenced code blocks, inline code, bold / italic / strikethrough,
 * ordered + unordered (nested) lists, blockquotes, tables, rules and links.
 */
import { Fragment, useMemo, type ReactNode } from 'react'

/** Headings deeper than this are rendered like `####`. */
const MAX_HEADING_LEVEL = 6

const HEADING_CLASS: Record<number, string> = {
  1: 'mt-5 mb-2 text-lg font-semibold text-white first:mt-0',
  2: 'mt-5 mb-2 text-base font-semibold text-white first:mt-0',
  3: 'mt-4 mb-2 text-sm font-semibold text-sky-200 first:mt-0',
  4: 'mt-4 mb-2 text-sm font-semibold text-slate-200 first:mt-0',
}

const LIST_CLASS = 'my-2 space-y-1 pl-5 text-[13px] leading-relaxed text-slate-300'

/** Safe link targets only; anything else is rendered as plain text. */
function safeHref(raw: string): string | null {
  const href = raw.trim()
  if (/^https?:\/\//i.test(href)) return href
  if (/^mailto:/i.test(href)) return href
  if (href.startsWith('/') && !href.startsWith('//')) return href
  return null
}

const INLINE_PATTERN =
  /\\([\\`*_{}[\]()#+\-.!>~])|`([^`\n]+)`|\*\*\*([^*\n]+)\*\*\*|\*\*([\s\S]+?)\*\*|(?<![\w*])\*([^*\n]+)\*(?![\w*])|__([^_\n]+)__|~~([^~\n]+)~~|\[([^\]]*)\]\(([^)\s]+)(?:\s+"[^"]*")?\)|(https?:\/\/[^\s<>"')]+)/g

/** Inline pass: code, emphasis and links -> React nodes. */
function parseInline(text: string, keyBase: string): ReactNode[] {
  const nodes: ReactNode[] = []
  let cursor = 0
  let index = 0

  INLINE_PATTERN.lastIndex = 0
  let match: RegExpExecArray | null
  while ((match = INLINE_PATTERN.exec(text)) !== null) {
    if (match.index > cursor) nodes.push(text.slice(cursor, match.index))
    const key = `${keyBase}-${index++}`
    const [full, escape, code, boldItalic, bold, italic, boldUnder, strike, linkText, linkHref, url] =
      match

    if (escape !== undefined) nodes.push(escape)
    else if (code !== undefined)
      nodes.push(
        <code
          key={key}
          className="rounded border border-slate-700/70 bg-slate-800/70 px-1 py-0.5 font-mono text-[0.9em] text-sky-200"
        >
          {code}
        </code>,
      )
    else if (boldItalic !== undefined)
      nodes.push(
        <strong key={key} className="font-semibold text-white italic">
          {boldItalic}
        </strong>,
      )
    else if (bold !== undefined)
      nodes.push(
        <strong key={key} className="font-semibold text-slate-100">
          {parseInline(bold, `${key}b`)}
        </strong>,
      )
    else if (italic !== undefined) nodes.push(<em key={key}>{parseInline(italic, `${key}i`)}</em>)
    else if (boldUnder !== undefined)
      nodes.push(
        <strong key={key} className="font-semibold text-slate-100">
          {boldUnder}
        </strong>,
      )
    else if (strike !== undefined)
      nodes.push(
        <span key={key} className="text-slate-500 line-through">
          {strike}
        </span>,
      )
    else if (linkHref !== undefined) {
      const href = safeHref(linkHref)
      if (href === null) {
        nodes.push(full)
      } else {
        nodes.push(
          <a
            key={key}
            href={href}
            target={href.startsWith('/') ? undefined : '_blank'}
            rel="noreferrer noopener nofollow"
            className="text-sky-400 underline decoration-sky-400/50 underline-offset-2 hover:text-sky-300"
          >
            {linkText || href}
          </a>,
        )
      }
    } else if (url !== undefined) {
      const href = safeHref(url)
      nodes.push(
        href === null ? (
          url
        ) : (
          <a
            key={key}
            href={href}
            target="_blank"
            rel="noreferrer noopener nofollow"
            className="break-all text-sky-400 underline decoration-sky-400/50 underline-offset-2 hover:text-sky-300"
          >
            {url}
          </a>
        ),
      )
    }

    cursor = match.index + full.length
  }

  if (cursor < text.length) nodes.push(text.slice(cursor))
  return nodes
}

interface ListItem {
  indent: number
  /** Bullet (`-`, `*`, `+`) or enumerator (`1.`, `1)`). */
  marker: string
  content: string
}

/** Split a `|`-delimited table row into trimmed cells. */
function splitRow(line: string): string[] {
  return line
    .trim()
    .replace(/^\|/, '')
    .replace(/\|$/, '')
    .split('|')
    .map((cell) => cell.trim())
}

const TABLE_SEPARATOR = /^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$/
/** Parse a run of lines into blocks (used for the document and blockquotes). */
function parseBlocks(lines: string[], keyBase: string): ReactNode[] {
  const blocks: ReactNode[] = []
  let i = 0
  let blockIndex = 0

  const nextKey = () => `${keyBase}-b${blockIndex++}`

  while (i < lines.length) {
    const line = lines[i]

    // Blank -------------------------------------------------------------
    if (!line.trim()) {
      i += 1
      continue
    }

    // Fenced code -------------------------------------------------------
    const fence = /^\s*(```|~~~)\s*([A-Za-z0-9+#-]*)\s*$/.exec(line)
    if (fence) {
      const marker = fence[1]
      const lang = fence[2] || ''
      const body: string[] = []
      i += 1
      while (i < lines.length && !new RegExp(`^\\s*${marker}\\s*$`).test(lines[i])) {
        body.push(lines[i])
        i += 1
      }
      i += 1 // closing fence (tolerated when missing)
      blocks.push(
        <pre
          key={nextKey()}
          className="my-3 overflow-auto rounded-lg border border-slate-800 bg-slate-950/80 p-3 font-mono text-[11.5px] leading-relaxed text-slate-200"
        >
          <code>{body.join('\n')}</code>
          {lang ? <span className="sr-only">{`code block: ${lang}`}</span> : null}
        </pre>,
      )
      continue
    }

    // Heading -----------------------------------------------------------
    const heading = /^\s{0,3}(#{1,6})\s+(.*?)\s*#*\s*$/.exec(line)
    if (heading) {
      const level = Math.min(heading[1].length, MAX_HEADING_LEVEL)
      const content = parseInline(heading[2], nextKey())
      const className = HEADING_CLASS[Math.min(level, 4)] ?? HEADING_CLASS[4]
      blocks.push(
        level === 1 ? (
          <h2 key={nextKey()} className={HEADING_CLASS[1]}>
            {content}
          </h2>
        ) : level === 2 ? (
          <h3 key={nextKey()} className={HEADING_CLASS[2]}>
            {content}
          </h3>
        ) : level === 3 ? (
          <h4 key={nextKey()} className={HEADING_CLASS[3]}>
            {content}
          </h4>
        ) : (
          <h5 key={nextKey()} className={className}>
            {content}
          </h5>
        ),
      )
      i += 1
      continue
    }

    // Horizontal rule ---------------------------------------------------
    if (/^\s{0,3}([-*_])\s*(\1\s*){2,}$/.test(line)) {
      blocks.push(<hr key={nextKey()} className="my-4 border-slate-800" />)
      i += 1
      continue
    }

    // Blockquote --------------------------------------------------------
    if (/^\s{0,3}>/.test(line)) {
      const inner: string[] = []
      while (i < lines.length && /^\s{0,3}>/.test(lines[i])) {
        inner.push(lines[i].replace(/^\s{0,3}>\s?/, ''))
        i += 1
      }
      blocks.push(
        <blockquote
          key={nextKey()}
          className="my-3 border-l-2 border-sky-500/50 bg-slate-900/40 py-2 pl-3 pr-2 text-[13px] text-slate-300"
        >
          {parseBlocks(inner, nextKey())}
        </blockquote>,
      )
      continue
    }

    // Table -------------------------------------------------------------
    if (line.includes('|') && i + 1 < lines.length && TABLE_SEPARATOR.test(lines[i + 1])) {
      const header = splitRow(line)
      i += 2
      const rows: string[][] = []
      while (i < lines.length && lines[i].includes('|') && lines[i].trim()) {
        rows.push(splitRow(lines[i]))
        i += 1
      }
      blocks.push(
        <div key={nextKey()} className="my-3 overflow-x-auto">
          <table className="w-full border-collapse text-[12.5px]">
            <thead>
              <tr>
                {header.map((cell, cellIndex) => (
                  <th
                    key={`${nextKey()}-h${cellIndex}`}
                    className="border border-slate-700/70 bg-slate-800/60 px-2 py-1.5 text-left font-semibold text-slate-200"
                  >
                    {parseInline(cell, `${nextKey()}-h${cellIndex}`)}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((row, rowIndex) => (
                <tr key={`${nextKey()}-r${rowIndex}`}>
                  {header.map((_, cellIndex) => (
                    <td
                      key={`${nextKey()}-r${rowIndex}c${cellIndex}`}
                      className="border border-slate-800 px-2 py-1.5 text-slate-300"
                    >
                      {parseInline(row[cellIndex] ?? '', `${nextKey()}-r${rowIndex}c${cellIndex}`)}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>,
      )
      continue
    }

    // Lists -------------------------------------------------------------
    const item = /^(\s*)([-*+]|\d+[.)])\s+(.*)$/.exec(line)
    if (item) {
      const collected: ListItem[] = []
      while (i < lines.length) {
        const match = /^(\s*)([-*+]|\d+[.)])\s+(.*)$/.exec(lines[i])
        if (match) {
          collected.push({ indent: match[1].length, marker: match[2], content: match[3] })
          i += 1
          continue
        }
        // Indented continuation of the previous item.
        if (lines[i].trim() && /^\s{2,}\S/.test(lines[i]) && collected.length) {
          collected[collected.length - 1].content += ` ${lines[i].trim()}`
          i += 1
          continue
        }
        break
      }
      blocks.push(renderList(collected, nextKey()))
      continue
    }

    // Paragraph ---------------------------------------------------------
    const paragraph: string[] = []
    while (
      i < lines.length &&
      lines[i].trim() &&
      !/^\s{0,3}(#{1,6}\s|>|```|~~~|([-*_])\s*(\2\s*){2,}$)/.test(lines[i]) &&
      !/^(\s*)([-*+]|\d+[.)])\s+/.test(lines[i])
    ) {
      paragraph.push(lines[i].trim())
      i += 1
    }
    const key = nextKey()
    blocks.push(
      <p key={key} className="my-2 text-[13px] leading-relaxed text-slate-300">
        {paragraph.map((text, lineIndex) => (
          <Fragment key={`${key}-l${lineIndex}`}>
            {lineIndex > 0 ? <br /> : null}
            {parseInline(text, `${key}-l${lineIndex}`)}
          </Fragment>
        ))}
      </p>,
    )
  }

  return blocks
}

/** Render a flat, indented list into nested `<ul>` / `<ol>` trees. */
function renderList(items: ListItem[], keyBase: string): ReactNode {
  const baseIndent = Math.min(...items.map((item) => item.indent))

  // Everything indented deeper than the shallowest item belongs to the item
  // above it, which is how `1. a\n   - b` becomes a nested list.
  const groups: Array<{ item: ListItem; nested: ListItem[] }> = []
  for (const entry of items) {
    if (entry.indent === baseIndent || groups.length === 0) groups.push({ item: entry, nested: [] })
    else groups[groups.length - 1].nested.push(entry)
  }

  const ordered = /^\d/.test(groups[0].item.marker)
  const className = `${LIST_CLASS} ${ordered ? 'list-decimal' : 'list-disc'}`

  const body = groups.map((group, index) => (
    <li key={`${keyBase}-i${index}`} className="leading-relaxed">
      {parseInline(group.item.content, `${keyBase}-i${index}`)}
      {group.nested.length ? renderList(group.nested, `${keyBase}-n${index}`) : null}
    </li>
  ))

  return ordered ? <ol className={className}>{body}</ol> : <ul className={className}>{body}</ul>
}

export interface MarkdownProps {
  /** Raw Markdown source. */
  source: string
  className?: string
}

export function Markdown({ source, className = '' }: MarkdownProps) {
  const rendered = useMemo(() => {
    if (!source.trim()) return null
    return parseBlocks(source.replace(/\r\n?/g, '\n').split('\n'), 'md')
  }, [source])

  if (rendered === null) {
    return (
      <p className="italic text-slate-500">This problem has no written statement.</p>
    )
  }

  return <div className={`max-w-none ${className}`}>{rendered}</div>
}

export default Markdown
