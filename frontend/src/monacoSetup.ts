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
// Hand-picked replacement for `monaco-editor/editor/editor.main.js`: that
// side-effect entry pulls in ~80 language definitions and the TypeScript /
// CSS / HTML / JSON language features (their workers add ~7 MB to dist).
// The judge only ever edits C/C++, so register just the C/C++ definition
// (registers both ids, grammar loads lazily via `import('./cpp.js')`) and
// the editor contributions copied verbatim from editor.main.js (lines
// 92-165). This keeps the base editor worker as the only emitted worker.
import 'monaco-editor/languages/definitions/cpp/register.js'
// Editor contributions (mirrors editor.main.js, minus language features).
import 'monaco-editor/editor/contrib/anchorSelect/browser/anchorSelect.js'
import 'monaco-editor/editor/contrib/bracketMatching/browser/bracketMatching.js'
import 'monaco-editor/editor/contrib/caretOperations/browser/transpose.js'
import 'monaco-editor/editor/contrib/clipboard/browser/clipboard.js'
import 'monaco-editor/editor/contrib/codeAction/browser/codeActionContributions.js'
import 'monaco-editor/editor/browser/widget/codeEditor/codeEditorWidget.js'
import 'monaco-editor/editor/contrib/codelens/browser/codelensController.js'
// The package exports map ('./*' -> './esm/vs/*.js') rewrites any bare
// subpath ending in `.css` to a non-existent `.css.js`, and monaco ships no
// dedicated CSS export pattern, so reach the two codicon stylesheets by file
// path (vite/client's `*.css` module declaration keeps tsc happy).
import '../node_modules/monaco-editor/esm/vs/base/browser/ui/codicons/codicon/codicon.css'
import 'monaco-editor/editor/contrib/colorPicker/browser/colorPickerContribution.js'
import 'monaco-editor/editor/contrib/comment/browser/comment.js'
import 'monaco-editor/editor/contrib/contextmenu/browser/contextmenu.js'
import 'monaco-editor/editor/contrib/cursorUndo/browser/cursorUndo.js'
import 'monaco-editor/editor/browser/widget/diffEditor/diffEditor.contribution.js'
import 'monaco-editor/editor/contrib/diffEditorBreadcrumbs/browser/contribution.js'
import 'monaco-editor/editor/contrib/dnd/browser/dnd.js'
import 'monaco-editor/editor/contrib/documentSymbols/browser/documentSymbols.js'
import 'monaco-editor/editor/contrib/dropOrPasteInto/browser/dropIntoEditorContribution.js'
import 'monaco-editor/features/find/register.js'
import 'monaco-editor/editor/contrib/floatingMenu/browser/floatingMenu.contribution.js'
import 'monaco-editor/editor/contrib/folding/browser/folding.js'
import 'monaco-editor/editor/contrib/fontZoom/browser/fontZoom.js'
import 'monaco-editor/editor/contrib/format/browser/formatActions.js'
import 'monaco-editor/editor/contrib/gotoError/browser/gotoError.js'
import 'monaco-editor/editor/standalone/browser/quickAccess/standaloneGotoLineQuickAccess.js'
import 'monaco-editor/editor/contrib/gotoSymbol/browser/link/goToDefinitionAtPosition.js'
import 'monaco-editor/editor/contrib/gpu/browser/gpuActions.js'
import 'monaco-editor/editor/contrib/hover/browser/hoverContribution.js'
import 'monaco-editor/editor/contrib/indentation/browser/indentation.js'
import 'monaco-editor/editor/contrib/inlayHints/browser/inlayHintsContribution.js'
import 'monaco-editor/editor/contrib/inlineCompletions/browser/inlineCompletions.contribution.js'
import 'monaco-editor/editor/contrib/inlineProgress/browser/inlineProgress.js'
import 'monaco-editor/editor/contrib/inPlaceReplace/browser/inPlaceReplace.js'
import 'monaco-editor/editor/contrib/insertFinalNewLine/browser/insertFinalNewLine.js'
import 'monaco-editor/editor/standalone/browser/inspectTokens/inspectTokens.js'
import 'monaco-editor/editor/standalone/browser/iPadShowKeyboard/iPadShowKeyboard.js'
import 'monaco-editor/editor/contrib/lineSelection/browser/lineSelection.js'
import 'monaco-editor/editor/contrib/linesOperations/browser/linesOperations.js'
import 'monaco-editor/editor/contrib/linkedEditing/browser/linkedEditing.js'
import 'monaco-editor/editor/contrib/links/browser/links.js'
import 'monaco-editor/editor/contrib/longLinesHelper/browser/longLinesHelper.js'
import 'monaco-editor/editor/contrib/middleScroll/browser/middleScroll.contribution.js'
import 'monaco-editor/editor/contrib/multicursor/browser/multicursor.js'
import 'monaco-editor/editor/contrib/parameterHints/browser/parameterHints.js'
import 'monaco-editor/editor/contrib/placeholderText/browser/placeholderText.contribution.js'
import 'monaco-editor/editor/standalone/browser/quickAccess/standaloneCommandsQuickAccess.js'
import 'monaco-editor/editor/standalone/browser/quickAccess/standaloneHelpQuickAccess.js'
import 'monaco-editor/editor/standalone/browser/quickAccess/standaloneGotoSymbolQuickAccess.js'
import 'monaco-editor/editor/contrib/readOnlyMessage/browser/contribution.js'
import 'monaco-editor/editor/standalone/browser/referenceSearch/standaloneReferenceSearch.js'
import 'monaco-editor/editor/contrib/rename/browser/rename.js'
import 'monaco-editor/editor/contrib/sectionHeaders/browser/sectionHeaders.js'
import 'monaco-editor/editor/contrib/semanticTokens/browser/viewportSemanticTokens.js'
import 'monaco-editor/editor/contrib/smartSelect/browser/smartSelect.js'
import 'monaco-editor/editor/contrib/snippet/browser/snippetController2.js'
import 'monaco-editor/editor/contrib/stickyScroll/browser/stickyScrollContribution.js'
import 'monaco-editor/editor/contrib/suggest/browser/suggestInlineCompletions.js'
import 'monaco-editor/editor/standalone/browser/toggleHighContrast/toggleHighContrast.js'
import 'monaco-editor/editor/contrib/toggleTabFocusMode/browser/toggleTabFocusMode.js'
import 'monaco-editor/editor/contrib/tokenization/browser/tokenization.js'
import 'monaco-editor/editor/contrib/unicodeHighlighter/browser/unicodeHighlighter.js'
import 'monaco-editor/editor/contrib/unusualLineTerminators/browser/unusualLineTerminators.js'
import 'monaco-editor/editor/contrib/wordHighlighter/browser/wordHighlighter.js'
import 'monaco-editor/editor/contrib/wordOperations/browser/wordOperations.js'
import 'monaco-editor/editor/contrib/wordPartOperations/browser/wordPartOperations.js'
import 'monaco-editor/editor/browser/coreCommands.js'
import 'monaco-editor/editor/contrib/caretOperations/browser/caretOperations.js'
import 'monaco-editor/editor/contrib/dropOrPasteInto/browser/copyPasteContribution.js'
import 'monaco-editor/editor/contrib/find/browser/findController.js'
import 'monaco-editor/editor/contrib/gotoSymbol/browser/goToCommands.js'
import 'monaco-editor/editor/contrib/gotoError/browser/markerSelectionStatus.js'
import 'monaco-editor/editor/contrib/semanticTokens/browser/documentSemanticTokens.js'
import 'monaco-editor/editor/contrib/suggest/browser/suggestController.js'
import 'monaco-editor/editor/common/standaloneStrings.js'
import '../node_modules/monaco-editor/esm/vs/base/browser/ui/codicons/codicon/codicon-modifiers.css'
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
