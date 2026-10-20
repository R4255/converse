import { App as AntApp, ConfigProvider, theme } from 'antd'
import { StrictMode, useEffect, useState } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import AppShell from './App'
import './index.css'

function Root() {
  const mq = window.matchMedia('(prefers-color-scheme: dark)')
  const [dark, setDark] = useState(mq.matches)
  useEffect(() => {
    const f = (e: MediaQueryListEvent) => setDark(e.matches)
    mq.addEventListener('change', f)
    return () => mq.removeEventListener('change', f)
  }, [mq])
  useEffect(() => { document.documentElement.dataset.theme = dark ? 'dark' : 'light' }, [dark])
  return (
    <ConfigProvider theme={{
      algorithm: dark ? theme.darkAlgorithm : theme.defaultAlgorithm,
      token: { colorPrimary: '#0c831f', borderRadius: 10, fontFamily: "Inter, system-ui, -apple-system, 'Segoe UI', sans-serif" },
    }}>
      <AntApp>
        <BrowserRouter><AppShell /></BrowserRouter>
      </AntApp>
    </ConfigProvider>
  )
}

createRoot(document.getElementById('root')!).render(<StrictMode><Root /></StrictMode>)
