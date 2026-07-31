import { useEffect, useRef } from 'react'
import { EditorState } from '@codemirror/state'
import { EditorView, keymap } from '@codemirror/view'
import { defaultKeymap, indentWithTab } from '@codemirror/commands'
import { autocompletion, closeBrackets, closeBracketsKeymap, completionKeymap } from '@codemirror/autocomplete'
import { syntaxHighlighting, defaultHighlightStyle, bracketMatching } from '@codemirror/language'
import { oneDark } from '@codemirror/theme-one-dark'
import { javascript } from '@codemirror/lang-javascript'
import { python } from '@codemirror/lang-python'
import { json } from '@codemirror/lang-json'
import { html } from '@codemirror/lang-html'
import { css } from '@codemirror/lang-css'
import { markdown } from '@codemirror/lang-markdown'
import { xml } from '@codemirror/lang-xml'
import { java } from '@codemirror/lang-java'
import { cpp } from '@codemirror/lang-cpp'
import { go } from '@codemirror/lang-go'
import { rust } from '@codemirror/lang-rust'
import { yaml } from '@codemirror/lang-yaml'

type Props = { value: string; onChange: (value: string) => void; fileName: string; enabled: boolean; onSave: () => void }

function languageFor(fileName: string) {
  const extension = fileName.split('.').pop()?.toLowerCase()
  if (extension === 'ts' || extension === 'tsx') return javascript({ typescript: true, jsx: extension === 'tsx' })
  if (extension === 'js' || extension === 'jsx') return javascript({ jsx: extension === 'jsx' })
  if (extension === 'py') return python()
  if (extension === 'json') return json()
  if (extension === 'html' || extension === 'htm') return html()
  if (extension === 'css') return css()
  if (extension === 'md' || extension === 'mdx') return markdown()
  if (extension === 'xml') return xml()
  if (extension === 'java') return java()
  if (extension === 'c' || extension === 'cpp' || extension === 'cc' || extension === 'h' || extension === 'hpp') return cpp()
  if (extension === 'go') return go()
  if (extension === 'rs') return rust()
  if (extension === 'yaml' || extension === 'yml') return yaml()
  return []
}

export default function CodeEditor({ value, onChange, fileName, enabled, onSave }: Props) {
  const hostRef = useRef<HTMLDivElement>(null)
  const viewRef = useRef<EditorView | null>(null)
  const valueRef = useRef(value)
  const onChangeRef = useRef(onChange)
  const onSaveRef = useRef(onSave)
  valueRef.current = value
  onChangeRef.current = onChange
  onSaveRef.current = onSave

  useEffect(() => {
    if (!hostRef.current) return
    const updateListener = EditorView.updateListener.of((update) => {
      if (update.docChanged) onChangeRef.current(update.state.doc.toString())
    })
    const saveBinding = keymap.of([{ key: 'Mod-s', run: () => { onSaveRef.current(); return true } }])
    const extensions = [
      EditorView.lineWrapping,
      keymap.of([...defaultKeymap, ...closeBracketsKeymap, ...completionKeymap, indentWithTab]),
      saveBinding,
      updateListener,
      EditorView.theme({ '&': { height: '100%', fontSize: 'var(--ui-font-size, 13px)' }, '.cm-scroller': { fontFamily: 'Consolas, "Cascadia Code", monospace' }, '.cm-gutters': { background: '#1e1e1e', color: '#6c7887', border: 'none' }, '.cm-content': { padding: '18px 0' }, '.cm-line': { padding: '0 16px' } }),
    ]
    if (enabled) extensions.push(oneDark, syntaxHighlighting(defaultHighlightStyle), bracketMatching(), closeBrackets(), autocompletion(), languageFor(fileName))
    const state = EditorState.create({ doc: valueRef.current, extensions })
    const view = new EditorView({ state, parent: hostRef.current })
    viewRef.current = view
    return () => view.destroy()
  }, [fileName, enabled])

  useEffect(() => {
    const view = viewRef.current
    if (view && value !== view.state.doc.toString()) view.dispatch({ changes: { from: 0, to: view.state.doc.length, insert: value } })
  }, [value])

  return <div className="code-editor-host" ref={hostRef} />
}
