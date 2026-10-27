/**
 * Monaco Editor bootstrap.
 *
 * The editor is bundled from the local `monaco-editor` package instead of being
 * pulled from a CDN at runtime, so the judge works on an air-gapped classroom
 * network and starts instantly after the first Vite pre-bundle.
 *
 * Import this module (side effect) before rendering `<Editor />`;
 * `@monaco-editor/react` then reuses the bundled instance.
 */
import * as monaco from 'monaco-editor/editor/editor.api.js'
// Side-effect entry: registers the browser contributions, all basic languages
// (including C/C++) and the editor/codicon stylesheets.
import 'monaco-editor/editor/editor.main.js'
import editorWorker from 'monaco-editor/editor/editor.worker.js?worker'
import { loader } from '@monaco-editor/react'

/** Custom dark theme that matches the console panels. */
export const EDITOR_THEME = 'codingpass-dark'

/** Font stack shared by Monaco and the terminal output panels. */
export const MONACO_FONT_FAMILY =
  "'JetBrains Mono', 'Fira Code', ui-monospace, SFMono-Regular, Menlo, Consolas, 'Liberation Mono', monospace"

// Monaco offloads tokenisation and text search to a web worker. C and C++ only
// need the base editor worker, so one factory covers every worker id.
// Assigned through `globalThis` because `MonacoEnvironment` is only a ambient
// *type* declaration - a bare assignment would throw in an ES module.
globalThis.MonacoEnvironment = {
  getWorker: () => new editorWorker(),
}

loader.config({ monaco })

monaco.editor.defineTheme(EDITOR_THEME, {
  base: 'vs-dark',
  inherit: true,
  rules: [
    { token: 'comment', foreground: '64748b', fontStyle: 'italic' },
    { token: 'keyword', foreground: '93c5fd' },
    { token: 'string', foreground: '86efac' },
    { token: 'number', foreground: 'fbbf24' },
    { token: 'identifier', foreground: 'e2e8f0' },
    { token: 'type', foreground: '5eead4' },
    { token: 'delimiter', foreground: '94a3b8' },
  ],
  colors: {
    'editor.background': '#0b1120',
    'editor.foreground': '#dbeafe',
    'editor.lineHighlightBackground': '#131c31',
    'editorLineNumber.foreground': '#3f4c6b',
    'editorLineNumber.activeForeground': '#93c5fd',
    'editor.selectionBackground': '#1d4ed8aa',
    'editor.inactiveSelectionBackground': '#1d4ed855',
    'editorCursor.foreground': '#38bdf8',
    'editorIndentGuide.background1': '#1e293b',
    'editorIndentGuide.activeBackground1': '#334155',
    'editorBracketMatch.background': '#0ea5e922',
    'editorBracketMatch.border': '#0ea5e9',
    'editorWidget.background': '#0f172a',
    'editorWidget.border': '#1e293b',
    'editorSuggestWidget.background': '#0f172a',
    'editorSuggestWidget.selectedBackground': '#1e293b',
    'scrollbarSlider.background': '#33415588',
    'scrollbarSlider.hoverBackground': '#475569aa',
    'scrollbarSlider.activeBackground': '#64748bcc',
  },
})

/** Editor configuration shared by both language tabs. */
export const EDITOR_OPTIONS: monaco.editor.IStandaloneEditorConstructionOptions = {
  fontSize: 13.5,
  fontFamily: MONACO_FONT_FAMILY,
  fontLigatures: false,
  lineHeight: 21,
  tabSize: 4,
  insertSpaces: true,
  autoIndent: 'full',
  minimap: { enabled: false },
  scrollBeyondLastLine: false,
  smoothScrolling: true,
  cursorBlinking: 'phase',
  cursorSmoothCaretAnimation: 'on',
  renderLineHighlight: 'all',
  renderWhitespace: 'selection',
  guides: { indentation: true, bracketPairs: 'active' },
  bracketPairColorization: { enabled: true },
  automaticLayout: true,
  padding: { top: 14, bottom: 14 },
  lineNumbersMinChars: 3,
  folding: true,
  wordWrap: 'off',
  overviewRulerLanes: 0,
  hideCursorInOverviewRuler: true,
  overviewRulerBorder: false,
  scrollbar: { verticalScrollbarSize: 10, horizontalScrollbarSize: 10, useShadows: false },
  fixedOverflowWidgets: true,
  renderValidationDecorations: 'on',
  unicodeHighlight: { ambiguousCharacters: false },
}

export { monaco }
