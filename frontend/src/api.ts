export type Variant = { variant_id: number; label: string; price: number; mrp: number; in_stock: boolean }
export type Product = {
  product_id: number; name: string; brand: string; category: string; subcategory: string
  rating: number; rating_count: number; tags: string[]; variants: Variant[]; description?: string
  reviews?: { author: string; rating: number; body: string }[]
}
export type CartLine = {
  variant_id: number; product_id: number; name: string; label: string; price: number; quantity: number; line_total: number
}
export type Cart = {
  items: CartLine[]; item_count: number; subtotal: number; delivery_fee: number; handling_fee: number
  total: number; free_delivery_gap: number; delivery_slot: string | null; delivery_label: string
}
export type OrderSummary = {
  order_id: number; placed_at: string; status: string; total: number; item_count: number; summary?: string; delivery?: string | null
}
export type Order = OrderSummary & { items: { variant_id: number; name: string; label: string; price: number; quantity: number }[] }
export type ShownItem = {
  ref: string; product_id: number; variant_id: number; name: string; label: string; price: number
  rating: number; in_stock: boolean; sizes: string[]; bought_before: boolean
}
export type UIAction = { type: string; payload: Record<string, any> }

let userId = Number(localStorage.getItem('converse.user') || 1)
export const getUserId = () => userId
export const setUserId = (id: number) => {
  userId = id
  try { localStorage.setItem('converse.user', String(id)) } catch { /* private mode */ }
}

async function req<T>(path: string, init: RequestInit = {}): Promise<T> {
  const r = await fetch(`/api${path}`, {
    ...init,
    headers: { 'content-type': 'application/json', 'x-user-id': String(userId), ...(init.headers || {}) },
  })
  const body = await r.json().catch(() => ({}))
  if (!r.ok) throw new Error(body.message || body.detail || `HTTP ${r.status}`)
  return body as T
}

export const api = {
  me: () => req<{ id: number; name: string; address: string }>('/me'),
  categories: () => req<{ name: string; subcategories: string[]; count: number }[]>('/categories'),
  products: (p: { q?: string; category?: string; sort?: string; limit?: number }) => {
    const qs = new URLSearchParams(Object.entries(p).filter(([, v]) => v != null && v !== '').map(([k, v]) => [k, String(v)]))
    return req<{ items: Product[]; timings_ms: Record<string, number> }>(`/products?${qs}`)
  },
  product: (id: number) => req<Product>(`/products/${id}`),
  cart: () => req<Cart>('/cart'),
  addToCart: (variant_id: number, quantity = 1) =>
    req<Cart>('/cart/items', { method: 'POST', body: JSON.stringify({ variant_id, quantity }) }),
  setQty: (variant_id: number, quantity: number) =>
    req<Cart>(`/cart/items/${variant_id}`, { method: 'PATCH', body: JSON.stringify({ quantity }) }),
  clearCart: () => req<Cart>('/cart', { method: 'DELETE' }),
  orders: () => req<OrderSummary[]>('/orders'),
  order: (id: number) => req<Order>(`/orders/${id}`),
  placeOrder: (key: string) => req<{ order_id: number; total: number }>('/orders', { method: 'POST', headers: { 'idempotency-key': key } }),
  slots: () => req<{ start: string | null; label: string }[]>('/delivery-slots'),
  setSlot: (start: string | null) => req<Cart>('/delivery-slot', { method: 'PUT', body: JSON.stringify({ start }) }),
  reminders: () => req<{ reminder_id: number; description: string; schedule: string; scheduled: boolean }[]>('/reminders'),
  notifications: () => req<{ id: number; kind: string; body: string; created_at: string }[]>('/notifications'),
  traces: () => req<any[]>('/traces'),
  trace: (id: string) => req<any>(`/traces/${id}`),
  evals: () => req<any>('/evals'),
  confirm: (session_id: string, token: string, approve: boolean) =>
    req<{ status: string; reply: string; ui_actions: UIAction[] }>('/agent/confirm', {
      method: 'POST', body: JSON.stringify({ session_id, token, approve }),
    }),
}

export type AgentEvent = { event: string; data: any }

/** POST + server-sent events (EventSource can't POST), parsed incrementally. */
export async function streamTurn(body: object, onEvent: (e: AgentEvent) => void): Promise<void> {
  const r = await fetch('/api/agent/turn', {
    method: 'POST',
    headers: { 'content-type': 'application/json', 'x-user-id': String(userId) },
    body: JSON.stringify(body),
  })
  if (!r.ok || !r.body) throw new Error(`agent error ${r.status}`)
  const reader = r.body.getReader()
  const decoder = new TextDecoder()
  let buf = ''
  for (;;) {
    const { done, value } = await reader.read()
    if (done) break
    buf += decoder.decode(value, { stream: true })
    let idx
    while ((idx = buf.search(/\r?\n\r?\n/)) >= 0) {
      const chunk = buf.slice(0, idx)
      buf = buf.slice(idx).replace(/^\r?\n\r?\n/, '')
      let event = 'message', data = ''
      for (const line of chunk.split(/\r?\n/)) {
        if (line.startsWith('event:')) event = line.slice(6).trim()
        else if (line.startsWith('data:')) data += line.slice(5).trim()
      }
      if (data) onEvent({ event, data: JSON.parse(data) })
    }
  }
}
