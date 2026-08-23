import { useCallback, useEffect, useState } from 'react'

type RagCollection = 'public' | 'private' | 'novel' | 'data'
const COLLECTIONS: Array<{ id: RagCollection; label: string; hint: string }> = [
  { id: 'public', label: '公开库', hint: '通用知识文档' },
  { id: 'private', label: '私有库', hint: '个人私有文档' },
  { id: 'novel', label: '小说库', hint: '长文本 / 小说' },
  { id: 'data', label: '资料库', hint: '数据资料' },
]

export default function KnowledgeBasePanel() {
  const [runtime, setRuntime] = useState<RagRuntime>({ status: 'starting' })
  const [stats, setStats] = useState<RagStats | null>(null)
  const [documents, setDocuments] = useState<RagDocList | null>(null)
  const [collection, setCollection] = useState<RagCollection>('public')
  const [query, setQuery] = useState('')
  const [results, setResults] = useState<RagSearchResult[] | null>(null)
  const [isSearching, setIsSearching] = useState(false)
  const [isIngesting, setIsIngesting] = useState(false)
  const [message, setMessage] = useState('')

  const load = useCallback(async () => {
    setRuntime({ status: 'starting' })
    try {
      const next = await window.ideaDesktop?.getRagRuntime()
      if (!next) throw new Error('内置 RAG 知识库不可用')
      setRuntime(next)
      if (next.status === 'ready') {
        const [nextStats, nextDocs] = await Promise.all([window.ideaDesktop?.getRagStats(), window.ideaDesktop?.getRagDocuments()])
        setStats(nextStats ?? null)
        setDocuments(nextDocs ?? null)
      }
    } catch (error) {
      setRuntime({ status: 'error', error: error instanceof Error ? error.message : 'RAG 知识库启动失败' })
    }
  }, [])

  useEffect(() => { void load() }, [load])

  const search = useCallback(async () => {
    const content = query.trim()
    if (!content) return
    setIsSearching(true)
    setMessage('')
    try {
      const response = await window.ideaDesktop?.ragSearch(collection, content, 5)
      if (!response) throw new Error('知识库检索服务不可用')
      setResults(response.results)
      setMessage(response.total_results ? `共 ${response.total_results} 条结果` : '未检索到相关内容')
    } catch (error) {
      setMessage(error instanceof Error ? error.message : '检索失败')
      setResults(null)
    } finally {
      setIsSearching(false)
    }
  }, [collection, query])

  const ingest = useCallback(async () => {
    setIsIngesting(true)
    setMessage('')
    try {
      const uploaded = await window.ideaDesktop?.ragIngest(collection)
      if (!uploaded) return
      if (!uploaded.length) return
      setMessage(`已导入 ${uploaded.length} 个文档，正在重建索引…`)
      await window.ideaDesktop?.ragRebuild('all')
      setMessage(`已导入 ${uploaded.length} 个文档，索引已重建`)
      await load()
    } catch (error) {
      setMessage(error instanceof Error ? error.message : '文档导入失败')
    } finally {
      setIsIngesting(false)
    }
  }, [collection, load])

  const rebuild = useCallback(async () => {
    setMessage('正在重建索引…')
    try {
      await window.ideaDesktop?.ragRebuild('all')
      setMessage('索引重建完成')
      await load()
    } catch (error) {
      setMessage(error instanceof Error ? error.message : '索引重建失败')
    }
  }, [load])

  const totalRecords = stats ? stats.private_records + stats.public_records + stats.novel_records + stats.data_records : 0
  const activeCollection = COLLECTIONS.find((item) => item.id === collection)!

  return <main className="rag-panel">
    <header className="rag-panel__header">
      <div><span>BUILT-IN RAG</span><strong>知识库 · 本地检索</strong></div>
      <button onClick={() => void load()} disabled={runtime.status === 'starting'}>刷新</button>
    </header>
    <section className="rag-panel__content">
      <div className={`rag-status rag-status--${runtime.status}`}>
        <strong>{runtime.status === 'ready' ? '服务就绪' : runtime.status === 'starting' ? '正在启动知识库…' : '知识库暂不可用'}</strong>
        <p>{runtime.status === 'starting' ? '正在加载嵌入模型与向量索引，首次启动可能需要一点时间。' : runtime.error}</p>
        {runtime.status === 'ready' && stats ? <div className="rag-status__meta"><span>模型：{stats.embedding_model.split('\\').pop()?.split('/').pop()}</span><span>向量：{totalRecords} 条</span></div> : null}
      </div>
      {runtime.status === 'ready' ? <>
        <nav className="rag-collections">{COLLECTIONS.map((item) => <button key={item.id} className={item.id === collection ? 'active' : ''} onClick={() => { setCollection(item.id); setResults(null) }}><strong>{item.label}</strong><small>{item.hint}</small></button>)}</nav>
        <div className="rag-search">
          <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder={`在「${activeCollection.label}」中检索…`} onKeyDown={(event) => { if (event.key === 'Enter') void search() }} />
          <button onClick={() => void search()} disabled={isSearching || !query.trim()}>{isSearching ? '检索中…' : '检索'}</button>
        </div>
        {results ? <div className="rag-results">{results.length ? results.map((result) => <article className="rag-result" key={`${result.rank}-${result.source}`}><header><span>#{result.rank}</span><strong>{result.source}</strong><small>{Math.round(result.similarity * 100)}%</small></header><p>{result.content}</p></article>) : <p className="rag-empty">没有匹配结果。</p>}</div> : <div className="rag-results rag-results--docs">{documents ? COLLECTIONS.map((item) => { const files = documents[item.id] ?? []; return <article className="rag-doc-group" key={item.id}><header><strong>{item.label}</strong><small>{files.length} 个文档</small></header><p>{files.length ? files.map((file) => file.replace(/^[0-9a-f]{8}_/, '')).join('、') : '暂无文档'}</p></article> }) : null}</div>}
        <footer className="rag-actions">
          <button onClick={() => void ingest()} disabled={isIngesting}>{isIngesting ? '导入中…' : '导入文档'}</button>
          <button onClick={() => void rebuild()}>重建索引</button>
          {message ? <span className="rag-message">{message}</span> : null}
        </footer>
      </> : <div className="rag-actions"><button onClick={() => void load()}>重试启动</button>{message ? <span className="rag-message">{message}</span> : null}</div>}
    </section>
  </main>
}
