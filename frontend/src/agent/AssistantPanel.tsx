import { CheckCircleFilled, CloseOutlined, LoadingOutlined, ReloadOutlined, RobotOutlined, ThunderboltOutlined } from '@ant-design/icons'
import { Button, Card, Empty, Flex, Space, Tag, Tooltip, Typography } from 'antd'
import { useEffect, useRef } from 'react'
import { Link } from 'react-router-dom'
import { useStore, type ChatMsg } from '../store'
import { useAgent } from './useAgent'

const TOOL_LABEL: Record<string, string> = {
  search_products: 'Searching products', get_product_details: 'Opening product', add_to_cart: 'Updating cart',
  update_cart_item: 'Updating cart', view_cart: 'Opening cart', clear_cart: 'Preparing to empty cart',
  find_orders: 'Looking up orders', reorder: 'Re-adding items', navigate: 'Navigating',
  place_order: 'Preparing your order', set_reminder: 'Scheduling reminder',
}

const EXAMPLES = [
  'show me my purchase of 5th january',
  'add my usual milk and brown bread, then take me to checkout',
  'best pizza ingredients',
  'reorder what I bought last friday',
  'sugar free snacks for my diabetic dad',
  'remind me every sunday to buy eggs',
]

function ConfirmCard({ msg, a }: { msg: ChatMsg; a: { payload: any } }) {
  const { confirm } = useAgent()
  const p = a.payload
  const state = msg.confirmState?.[p.token]
  return (
    <Card size="small" className="confirm-card" title={<Space><ThunderboltOutlined />{p.title}</Space>}>
      {(p.details?.lines || []).map((l: string) => <div key={l} className="muted">{l}</div>)}
      {p.details?.delivery && <div className="muted" style={{ marginTop: 6 }}>Delivery: {p.details.delivery}</div>}
      {p.details?.payment && <div className="muted">Payment: {p.details.payment}</div>}
      <Flex gap={8} style={{ marginTop: 10 }}>
        {state ? (
          <Tag color={state === 'approved' ? 'green' : state === 'rejected' ? 'default' : 'red'}>{state}</Tag>
        ) : (
          <>
            <Button type="primary" size="small" onClick={() => confirm(msg.id, p.token, true)}>Confirm</Button>
            <Button size="small" onClick={() => confirm(msg.id, p.token, false)}>Cancel</Button>
          </>
        )}
      </Flex>
    </Card>
  )
}

function Message({ m }: { m: ChatMsg }) {
  const { send } = useAgent()
  if (m.role === 'user') return <div className="bubble user">{m.text}</div>
  return (
    <div className="bubble assistant">
      {m.steps.length > 0 && (
        <div className="steps">
          {m.steps.map((s, i) => (
            <div key={i} className="step">
              {s.status === 'running' ? <LoadingOutlined /> : <CheckCircleFilled className="ok" />}
              <span>{s.status === 'running' ? TOOL_LABEL[s.tool] || s.tool : s.summary || TOOL_LABEL[s.tool]}</span>
            </div>
          ))}
        </div>
      )}
      {m.pending && !m.text && m.steps.length === 0 && <div className="muted"><LoadingOutlined /> Thinking…</div>}
      {m.text && <div>{m.text}</div>}
      {m.actions.filter((a) => a.type === 'confirm').map((a) => <ConfirmCard key={a.payload.token} msg={m} a={a} />)}
      {m.actions.filter((a) => a.type === 'clarify').map((a, i) => (
        <Flex key={i} wrap gap={6} style={{ marginTop: 8 }}>
          {a.payload.options.map((o: any) => (
            <Button key={o.value} size="small" shape="round" onClick={() => send(o.value)}>{o.label}</Button>
          ))}
        </Flex>
      ))}
      {m.meta?.trace_id && (
        <div className="meta">
          <Tooltip title={m.meta.route === 'fast' ? 'Handled by the deterministic router (no LLM call)' : 'LLM agent loop'}>
            <Tag bordered={false} color={m.meta.route === 'fast' ? 'cyan' : 'purple'}>{m.meta.route}</Tag>
          </Tooltip>
          <span>{((m.meta.latency_ms || 0) / 1000).toFixed(1)}s</span>
          <Link to={`/dev/traces?id=${m.meta.trace_id}`}>trace</Link>
        </div>
      )}
    </div>
  )
}

export default function AssistantPanel() {
  const { chat, setPanel, resetSession } = useStore()
  const { send } = useAgent()
  const end = useRef<HTMLDivElement>(null)
  useEffect(() => {
    // Braces matter: newer Chrome returns a Promise from scrollIntoView, which React would treat as a cleanup fn.
    end.current?.scrollIntoView({ behavior: 'smooth' })
  }, [chat])

  return (
    <aside className="assistant-panel">
      <Flex justify="space-between" align="center" className="assistant-head">
        <Space><RobotOutlined /><Typography.Text strong>Assistant</Typography.Text></Space>
        <Space size={0}>
          <Tooltip title="New conversation"><Button type="text" icon={<ReloadOutlined />} onClick={resetSession} /></Tooltip>
          <Button type="text" icon={<CloseOutlined />} onClick={() => setPanel(false)} />
        </Space>
      </Flex>
      <div className="assistant-body">
        {chat.length === 0 ? (
          <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="Tell me what you need. I can search, fill your cart, find past orders and take you anywhere in the app.">
            <Flex vertical gap={6}>
              {EXAMPLES.map((e) => <Button key={e} size="small" onClick={() => send(e)} className="example">{e}</Button>)}
            </Flex>
          </Empty>
        ) : chat.map((m) => <Message key={m.id} m={m} />)}
        <div ref={end} />
      </div>
    </aside>
  )
}
