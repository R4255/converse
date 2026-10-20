import { create } from 'zustand'
import type { Cart, OrderSummary, ShownItem, UIAction } from './api'

export type Step = { tool: string; status: 'running' | 'done'; summary?: string; args?: any }
export type ChatMsg = {
  id: string
  role: 'user' | 'assistant'
  text: string
  steps: Step[]
  actions: UIAction[]
  pending?: boolean
  meta?: { trace_id?: string; latency_ms?: number; route?: string }
  confirmState?: Record<string, 'approved' | 'rejected' | 'error'>
}

type State = {
  sessionId: string
  cart: Cart | null
  chat: ChatMsg[]
  panelOpen: boolean
  agentResults: { title: string; items: ShownItem[]; timings?: Record<string, number> } | null
  agentOrders: { title: string; orders: OrderSummary[] } | null
  busy: boolean
  setCart: (c: Cart) => void
  setPanel: (o: boolean) => void
  push: (m: ChatMsg) => void
  patch: (id: string, f: (m: ChatMsg) => ChatMsg) => void
  set: (p: Partial<State>) => void
  resetSession: () => void
}

const newSession = () => `web-${Math.random().toString(36).slice(2, 10)}`

export const useStore = create<State>((set) => ({
  sessionId: newSession(),
  cart: null,
  chat: [],
  panelOpen: false,
  agentResults: null,
  agentOrders: null,
  busy: false,
  setCart: (cart) => set({ cart }),
  setPanel: (panelOpen) => set({ panelOpen }),
  push: (m) => set((s) => ({ chat: [...s.chat, m] })),
  patch: (id, f) => set((s) => ({ chat: s.chat.map((m) => (m.id === id ? f(m) : m)) })),
  set: (p) => set(p),
  resetSession: () => set({ sessionId: newSession(), chat: [], agentResults: null, agentOrders: null }),
}))
