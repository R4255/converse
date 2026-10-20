import { App } from 'antd'
import { useCallback } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import { api, streamTurn, type UIAction } from '../api'
import { useStore } from '../store'

const uid = () => Math.random().toString(36).slice(2, 12)

/** Sends a turn to the agent, streams progress into the chat, and executes UI actions. */
export function useAgent() {
  const navigate = useNavigate()
  const location = useLocation()
  const { message } = App.useApp()
  const store = useStore

  const apply = useCallback((a: UIAction) => {
    const s = store.getState()
    switch (a.type) {
      case 'navigate':
        navigate(a.payload.path)
        break
      case 'show_products':
        s.set({ agentResults: { title: a.payload.title, items: a.payload.items, timings: a.payload.timings_ms } })
        navigate(`/search?q=${encodeURIComponent(a.payload.query || '')}&from=assistant`)
        break
      case 'show_orders':
        s.set({ agentOrders: { title: a.payload.title, orders: a.payload.orders } })
        break
      case 'refresh_cart':
        s.setCart(a.payload.cart)
        break
      case 'toast':
        message.open({ type: a.payload.level === 'success' ? 'success' : 'info', content: a.payload.text })
        break
      // 'confirm' and 'clarify' render inside the assistant panel
    }
  }, [navigate, message, store])

  const send = useCallback(async (text: string) => {
    const s = store.getState()
    if (!text.trim() || s.busy) return
    s.set({ busy: true, panelOpen: true })
    s.push({ id: uid(), role: 'user', text, steps: [], actions: [] })
    const id = uid()
    s.push({ id, role: 'assistant', text: '', steps: [], actions: [], pending: true })
    try {
      await streamTurn(
        { session_id: s.sessionId, turn_id: uid(), message: text, context: { route: location.pathname } },
        ({ event, data }) => {
          const st = store.getState()
          if (event === 'step') {
            st.patch(id, (m) => {
              const steps = [...m.steps]
              const last = steps.findLastIndex((x) => x.tool === data.tool && x.status === 'running')
              if (data.status === 'done' && last >= 0) steps[last] = { ...steps[last], status: 'done', summary: data.summary }
              else steps.push({ tool: data.tool, status: data.status, summary: data.summary, args: data.args })
              return { ...m, steps }
            })
          } else if (event === 'ui_action') {
            st.patch(id, (m) => ({ ...m, actions: [...m.actions, data] }))
            apply(data)
          } else if (event === 'message') {
            st.patch(id, (m) => ({ ...m, text: data.text }))
          } else if (event === 'done') {
            st.patch(id, (m) => ({ ...m, pending: false, meta: data }))
          }
        },
      )
    } catch (e: any) {
      store.getState().patch(id, (m) => ({ ...m, pending: false, text: `Sorry, I couldn't reach the assistant (${e.message}).` }))
    } finally {
      store.getState().set({ busy: false })
    }
  }, [apply, location.pathname, store])

  const confirm = useCallback(async (msgId: string, token: string, approve: boolean) => {
    const s = store.getState()
    try {
      const r = await api.confirm(s.sessionId, token, approve)
      s.patch(msgId, (m) => ({ ...m, confirmState: { ...m.confirmState, [token]: approve ? 'approved' : 'rejected' } }))
      s.push({ id: uid(), role: 'assistant', text: r.reply, steps: [], actions: r.ui_actions })
      r.ui_actions.forEach(apply)
    } catch (e: any) {
      s.patch(msgId, (m) => ({ ...m, confirmState: { ...m.confirmState, [token]: 'error' } }))
      message.error(e.message)
    }
  }, [apply, message, store])

  return { send, confirm }
}
