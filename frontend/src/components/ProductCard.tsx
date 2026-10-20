import { MinusOutlined, PlusOutlined, StarFilled } from '@ant-design/icons'
import { App, Button, Card, Flex, Select, Tag, Typography } from 'antd'
import { useState } from 'react'
import { Link } from 'react-router-dom'
import { api, type Product, type Variant } from '../api'
import { useStore } from '../store'

export const CATEGORY_EMOJI: Record<string, string> = {
  'Dairy & Eggs': '🥛', 'Fruits & Vegetables': '🥦', Bakery: '🍞', Staples: '🌾', Snacks: '🍿',
  Beverages: '🥤', Breakfast: '🥣', 'Instant Food': '🍜', Frozen: '🧊', 'Meat & Seafood': '🍗',
  'Personal Care': '🧴', Household: '🧽', 'Baby Care': '🍼',
}

export function Thumb({ category, size = 72 }: { category: string; size?: number }) {
  return <div className="thumb" style={{ width: size, height: size, fontSize: size * 0.5 }}>{CATEGORY_EMOJI[category] || '🛒'}</div>
}

export function QtyControl({ variant }: { variant: Variant }) {
  const { message } = App.useApp()
  const { cart, setCart } = useStore()
  const line = cart?.items.find((l) => l.variant_id === variant.variant_id)
  const run = async (p: Promise<any>) => {
    try { setCart(await p) } catch (e: any) { message.error(e.message) }
  }
  if (!variant.in_stock) return <Tag>Out of stock</Tag>
  if (!line) return <Button type="primary" ghost size="small" onClick={() => run(api.addToCart(variant.variant_id))}>ADD</Button>
  return (
    <Flex align="center" className="qty">
      <Button size="small" type="primary" icon={<MinusOutlined />} onClick={() => run(api.setQty(variant.variant_id, line.quantity - 1))} />
      <span>{line.quantity}</span>
      <Button size="small" type="primary" icon={<PlusOutlined />} onClick={() => run(api.addToCart(variant.variant_id))} />
    </Flex>
  )
}

export default function ProductCard({ p, badge, preferVariant }: { p: Product; badge?: string; preferVariant?: number }) {
  const [vid, setVid] = useState(preferVariant ?? (p.variants.find((v) => v.in_stock) || p.variants[0]).variant_id)
  const v = p.variants.find((x) => x.variant_id === vid) || p.variants[0]
  const off = Math.round((1 - v.price / v.mrp) * 100)
  return (
    <Card size="small" className="product-card" hoverable>
      {badge && <span className="badge-ref">{badge}</span>}
      <Link to={`/product/${p.product_id}`}>
        <Flex justify="center"><Thumb category={p.category} /></Flex>
        <Typography.Paragraph ellipsis={{ rows: 2 }} className="pname">{p.name}</Typography.Paragraph>
      </Link>
      <div className="rating"><StarFilled /> {p.rating} <span className="muted">({p.rating_count})</span></div>
      {p.variants.length > 1 ? (
        <Select size="small" value={vid} onChange={setVid} style={{ width: '100%', margin: '6px 0' }}
          options={p.variants.map((x) => ({ value: x.variant_id, label: `${x.label} · ₹${x.price}` }))} />
      ) : <div className="muted label">{v.label}</div>}
      <Flex justify="space-between" align="center">
        <div>
          <b>₹{v.price}</b> {off > 0 && <span className="mrp">₹{v.mrp}</span>}
        </div>
        <QtyControl variant={v} />
      </Flex>
    </Card>
  )
}
