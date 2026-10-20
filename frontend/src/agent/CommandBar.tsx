import { AudioOutlined, SendOutlined, StarFilled } from '@ant-design/icons'
import { Button, Input, type InputRef } from 'antd'
import { useEffect, useRef, useState } from 'react'
import { useStore } from '../store'
import { useAgent } from './useAgent'

/** The one input that can do anything the app can do. ⌘K / Ctrl+K focuses it. */
export default function CommandBar() {
  const [text, setText] = useState('')
  const busy = useStore((s) => s.busy)
  const { send } = useAgent()
  const ref = useRef<InputRef>(null)
  const [listening, setListening] = useState(false)

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
        e.preventDefault()
        ref.current?.focus()
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])

  const submit = () => {
    const t = text.trim()
    if (!t) return
    setText('')
    void send(t)
  }

  // Browser speech recognition when available (Chrome/Edge). Purely optional.
  const SR = (window as any).SpeechRecognition || (window as any).webkitSpeechRecognition
  const listen = () => {
    if (!SR) return
    const rec = new SR()
    rec.lang = 'en-IN'
    rec.onresult = (e: any) => { const t = e.results[0][0].transcript; setListening(false); void send(t) }
    rec.onend = () => setListening(false)
    setListening(true)
    rec.start()
  }

  return (
    <Input
      ref={ref}
      size="large"
      className="command-bar"
      value={text}
      onChange={(e) => setText(e.target.value)}
      onPressEnter={submit}
      disabled={busy}
      prefix={<StarFilled className="spark" />}
      placeholder="Ask or tell me anything: “add my usual milk and take me to checkout”"
      suffix={
        <>
          {SR && <Button type="text" size="small" icon={<AudioOutlined />} onClick={listen} danger={listening} />}
          <span className="kbd">⌘K</span>
          <Button type="primary" size="small" icon={<SendOutlined />} loading={busy} onClick={submit} />
        </>
      }
    />
  )
}
