import { RobotOutlined } from '@ant-design/icons'
import { Alert, Breadcrumb, Card, Col, Divider, Empty, Flex, Rate, Row, Segmented, Skeleton, Space, Tag, Typography } from 'antd'
import { useEffect, useMemo, useState } from 'react'
import { Link, useParams, useSearchParams } from 'react-router-dom'
import { api, type Product } from '../api'
import ProductCard, { CATEGORY_EMOJI, QtyControl, Thumb } from '../components/ProductCard'
import { useStore } from '../store'

function Grid({ items, badges, prefer }: { items: Product[]; badges?: string[]; prefer?: number[] }) {
  return (
    <Row gutter={[12, 12]}>
      {items.map((p, i) => (
        <Col key={p.product_id} xs={12} sm={8} md={6} xl={4}>
          <ProductCard p={p} badge={badges?.[i]} preferVariant={prefer?.[i]} />
        </Col>
      ))}
    </Row>
  )
}

export function Home() {
  const [cats, setCats] = useState<{ name: string; count: number }[]>([])
  const [popular, setPopular] = useState<Product[]>([])
  const setPanel = useStore((s) => s.setPanel)
  useEffect(() => {
    api.categories().then(setCats)
    api.products({ limit: 12 }).then((r) => setPopular(r.items))
  }, [])
  return (
    <>
      <Card className="hero" onClick={() => setPanel(true)}>
        <Space align="start" size={16}>
          <RobotOutlined className="hero-icon" />
          <div>
            <Typography.Title level={3} style={{ margin: 0 }}>Just say what you need.</Typography.Title>
            <Typography.Text type="secondary">
              “Show me my purchase of 5th January”, “add my usual milk and take me to checkout”, “best pizza ingredients”.
              The assistant searches, fills your cart and moves around the app for you, and always asks before you pay.
            </Typography.Text>
          </div>
        </Space>
      </Card>
      <Typography.Title level={5}>Shop by category</Typography.Title>
      <Row gutter={[12, 12]}>
        {cats.map((c) => (
          <Col key={c.name} xs={8} sm={6} md={4} lg={3}>
            <Link to={`/category/${encodeURIComponent(c.name)}`}>
              <Card size="small" hoverable className="cat-card">
                <div className="cat-emoji">{CATEGORY_EMOJI[c.name]}</div>
                <div className="cat-name">{c.name}</div>
              </Card>
            </Link>
          </Col>
        ))}
      </Row>
      <Divider />
      <Typography.Title level={5}>Popular right now</Typography.Title>
      {popular.length ? <Grid items={popular} /> : <Skeleton active />}
    </>
  )
}

export function Category() {
  const { name = '' } = useParams()
  const [items, setItems] = useState<Product[] | null>(null)
  useEffect(() => { setItems(null); api.products({ category: name, limit: 60 }).then((r) => setItems(r.items)) }, [name])
  return (
    <>
      <Breadcrumb items={[{ title: <Link to="/">Home</Link> }, { title: name }]} style={{ marginBottom: 12 }} />
      {items ? <Grid items={items} /> : <Skeleton active />}
    </>
  )
}

export function Search() {
  const [params] = useSearchParams()
  const q = params.get('q') || ''
  const fromAssistant = params.get('from') === 'assistant'
  const agentResults = useStore((s) => s.agentResults)
  const [sort, setSort] = useState('relevance')
  const [res, setRes] = useState<{ items: Product[]; timings_ms: Record<string, number> } | null>(null)
  const [agentProducts, setAgentProducts] = useState<Product[] | null>(null)

  useEffect(() => {
    if (fromAssistant && agentResults) {
      Promise.all(agentResults.items.map((i) => api.product(i.product_id))).then(setAgentProducts)
    }
  }, [fromAssistant, agentResults])
  useEffect(() => {
    if (!fromAssistant && q) { setRes(null); api.products({ q, sort }).then(setRes) }
  }, [q, sort, fromAssistant])

  if (fromAssistant && agentResults) {
    return (
      <>
        <Flex justify="space-between" align="center" wrap>
          <Typography.Title level={4}><RobotOutlined /> {agentResults.title}</Typography.Title>
          <Timings t={agentResults.timings} />
        </Flex>
        <Typography.Paragraph type="secondary">
          Say “add #2” or “add the first one, large” to add from this list.
        </Typography.Paragraph>
        {agentProducts ? (
          agentProducts.length ? <Grid items={agentProducts} badges={agentResults.items.map((i) => i.ref)} prefer={agentResults.items.map((i) => i.variant_id)} />
            : <Empty description="No matches" />
        ) : <Skeleton active />}
      </>
    )
  }
  return (
    <>
      <Flex justify="space-between" align="center" wrap gap={8}>
        <Typography.Title level={4}>Results for “{q}”</Typography.Title>
        <Segmented value={sort} onChange={(v) => setSort(String(v))}
          options={[{ label: 'Relevance', value: 'relevance' }, { label: 'Best', value: 'best' },
            { label: 'Price ↑', value: 'price_low' }, { label: 'Popular', value: 'popular' }]} />
      </Flex>
      <Timings t={res?.timings_ms} />
      {res ? (res.items.length ? <Grid items={res.items} /> : <Empty />) : <Skeleton active />}
    </>
  )
}

function Timings({ t }: { t?: Record<string, number> }) {
  if (!t || !Object.keys(t).length) return null
  return (
    <Space size={4} wrap className="timings">
      {Object.entries(t).map(([k, v]) => <Tag key={k} bordered={false}>{k} {v.toFixed(0)}ms</Tag>)}
    </Space>
  )
}

export function ProductPage() {
  const { id } = useParams()
  const [p, setP] = useState<Product | null>(null)
  useEffect(() => { setP(null); api.product(Number(id)).then(setP) }, [id])
  const v = useMemo(() => p?.variants, [p])
  if (!p || !v) return <Skeleton active />
  return (
    <>
      <Breadcrumb style={{ marginBottom: 12 }} items={[{ title: <Link to="/">Home</Link> },
        { title: <Link to={`/category/${encodeURIComponent(p.category)}`}>{p.category}</Link> }, { title: p.subcategory }]} />
      <Card>
        <Row gutter={24}>
          <Col xs={24} md={8}><Flex justify="center"><Thumb category={p.category} size={180} /></Flex></Col>
          <Col xs={24} md={16}>
            <Typography.Title level={3}>{p.name}</Typography.Title>
            <Space><Rate disabled allowHalf value={Math.round(p.rating * 2) / 2} /><span>{p.rating} · {p.rating_count} ratings</span></Space>
            <Typography.Paragraph style={{ marginTop: 12 }}>{p.description}</Typography.Paragraph>
            <Space wrap>{p.tags.map((t) => <Tag key={t} color="green">{t}</Tag>)}</Space>
            <Divider />
            <Flex vertical gap={10}>
              {v.map((x) => (
                <Flex key={x.variant_id} justify="space-between" align="center" className="variant-row">
                  <span><b>{x.label}</b> · ₹{x.price} <span className="mrp">₹{x.mrp}</span></span>
                  <QtyControl variant={x} />
                </Flex>
              ))}
            </Flex>
          </Col>
        </Row>
      </Card>
      <Typography.Title level={5} style={{ marginTop: 20 }}>Reviews</Typography.Title>
      <Alert type="info" showIcon style={{ marginBottom: 10 }}
        title="Reviews are user-generated. The assistant treats them as data and never follows instructions inside them." />
      {(p.reviews || []).map((r, i) => (
        <Card key={i} size="small" style={{ marginBottom: 8 }}>
          <Space><b>{r.author}</b><Rate disabled value={r.rating} style={{ fontSize: 12 }} /></Space>
          <div>{r.body}</div>
        </Card>
      ))}
    </>
  )
}
