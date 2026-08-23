import { useCallback, useEffect, useState } from 'react'

type NekoRuntime = { status: 'starting' | 'ready' | 'error' | 'stopped'; url?: string; error?: string }

export default function NekoPanel() {
  const [runtime, setRuntime] = useState<NekoRuntime>({ status: 'starting' })
  const load = useCallback(async () => {
    setRuntime({ status: 'starting' })
    try {
      const next = await window.ideaDesktop?.getNekoRuntime()
      if (!next) throw new Error('N.E.K.O 运行时不可用')
      setRuntime(next)
    } catch (error) {
      setRuntime({ status: 'error', error: error instanceof Error ? error.message : 'N.E.K.O 启动失败' })
    }
  }, [])

  useEffect(() => { void load() }, [load])

  return <main className="neko-panel">
    <header className="neko-panel__header">
      <div><span>N.E.K.O RUNTIME</span><strong>角色与沉浸式交互</strong></div>
      <button onClick={() => void load()} disabled={runtime.status === 'starting'}>重新连接</button>
    </header>
    <section className="neko-panel__content">
      {runtime.status === 'ready' && runtime.url ? <iframe title="N.E.K.O 角色与聊天" src={runtime.url} allow="microphone; camera; display-capture; autoplay" /> : <div className="neko-panel__state"><strong>{runtime.status === 'starting' ? '正在启动 N.E.K.O…' : 'N.E.K.O 暂不可用'}</strong><p>{runtime.status === 'starting' ? '正在准备角色、记忆、Agent 与插件运行时。' : runtime.error}</p>{runtime.status === 'error' ? <button onClick={() => void load()}>重试</button> : null}</div>}
    </section>
  </main>
}
