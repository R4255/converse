import { BellOutlined, ClockCircleOutlined, RobotOutlined, ShoppingCartOutlined } from '@ant-design/icons'
import { App, Button, Card, Col, Descriptions, Divider, Empty, Flex, List, Result, Row, Select, Skeleton, Table, Tag, Typography } from 'antd'
import dayjs from 'dayjs'
import { useEffect, useMemo, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { api, type Order, type OrderSummary } from '../api'
import { QtyControl } from '../components/ProductCard'
import { useStore } from '../store'

function CartLines({ editable = true }: { editable?: boolean }) {
  const cart = useStore((s) => s.cart)
  if (!cart) return <Skeleton active />
  if (!cart.items.length) return <Empty description="Your cart is empty" />
  return (
    <List
      dataSource={cart.items}
      renderItem={(l, i) => (
        <List.Item extra={editable ? <QtyControl variant={{ variant_id: l.variant_id, label: l.label, price: l.price, mrp: l.price, in_stock: true }} /> : `× ${l.quantity}`}>
          <List.Item.Meta
            avatar={<Tag bordered={false}>#{i + 1}</Tag>}
            title={<Link to={`/product/${l.product_id}`}>{l.name}</Link>}
            description={`${l.label} · ₹${l.price} · line ₹${l.line_total}`}
          />
        </List.Item>
      )}
    />
  )
}

function Bill() {
  const cart = useStore((s) => s.cart)
  if (!cart?.items.length) return null
  return (
    <Descriptions column={1} size="small" bordered>
      <Descriptions.Item label="Items total">₹{cart.subtotal}</Descriptions.Item>
      <Descriptions.Item label="Delivery">{cart.delivery_fee ? `₹${cart.delivery_fee}` : <Tag color="green">FREE</Tag>}</Descriptions.Item>
      <Descriptions.Item label="Handling">₹{cart.handling_fee}</Descriptions.Item>
      <Descriptions.Item label={<b>To pay</b>}><b>₹{cart.total}</b></Descriptions.Item>
    </Descriptions>
  )
}

export function CartPage() {
  const cart = useStore((s) => s.cart)
  const nav = useNavigate()
  return (
    <Row gutter={20}>
      <Col xs={24} md={15}>
        <Typography.Title level={4}><ShoppingCartOutlined /> Cart</Typography.Title>
        {!!cart?.free_delivery_gap && <Tag color="orange" style={{ marginBottom: 8 }}>Add ₹{cart.free_delivery_gap} more for free delivery</Tag>}
        <Card><CartLines /></Card>
      </Col>
      <Col xs={24} md={9}>
        <Typography.Title level={4}>Bill</Typography.Title>
        <Bill />
        {!!cart?.items.length && <Button type="primary" block size="large" style={{ marginTop: 12 }} onClick={() => nav('/checkout')}>Proceed to checkout</Button>}
      </Col>
    </Row>
  )
}

export function Checkout() {
  const { cart, setCart } = useStore()
  const [me, setMe] = useState<{ name: string; address: string } | null>(null)
  const [placing, setPlacing] = useState(false)
  const [slots, setSlots] = useState<{ start: string | null; label: string }[]>([])
  const { message } = App.useApp()
  const nav = useNavigate()
  // One key per checkout visit: double clicks / retries can never create two orders.
  const idemKey = useMemo(() => crypto.randomUUID(), [])
  useEffect(() => { api.me().then(setMe); api.slots().then(setSlots) }, [])
  const place = async () => {
    setPlacing(true)
    try {
      const o = await api.placeOrder(idemKey)
      setCart(await api.cart())
      message.success(`Order #${o.order_id} placed`)
      nav(`/orders/${o.order_id}`)
    } catch (e: any) { message.error(e.message) } finally { setPlacing(false) }
  }
  if (cart && !cart.items.length) return <Result status="info" title="Nothing to check out" extra={<Link to="/">Continue shopping</Link>} />
  return (
    <Row gutter={20}>
      <Col xs={24} md={15}>
        <Typography.Title level={4}>Checkout</Typography.Title>
        <Card title="Deliver to" size="small" style={{ marginBottom: 12 }}>{me ? <><b>{me.name}</b><div>{me.address}</div></> : <Skeleton active />}</Card>
        <Card title="Delivery time" size="small" style={{ marginBottom: 12 }}>
          <Select style={{ width: '100%' }} value={cart?.delivery_slot ?? null} loading={!slots.length}
            options={slots.map((s) => ({ value: s.start, label: s.label }))}
            onChange={async (v) => setCart(await api.setSlot(v))} />
        </Card>
        <Card title="Items" size="small"><CartLines editable={false} /></Card>
      </Col>
      <Col xs={24} md={9}>
        <Typography.Title level={4}>Payment</Typography.Title>
        <Card size="small" style={{ marginBottom: 12 }}>Cash on delivery</Card>
        <Bill />
        <Button type="primary" block size="large" style={{ marginTop: 12 }} loading={placing} onClick={place}>Place order · ₹{cart?.total}</Button>
      </Col>
    </Row>
  )
}

export function Orders() {
  const agentOrders = useStore((s) => s.agentOrders)
  const set = useStore((s) => s.set)
  const [all, setAll] = useState<OrderSummary[] | null>(null)
  useEffect(() => { api.orders().then(setAll) }, [])
  const showing = agentOrders ? agentOrders.orders : all
  return (
    <>
      <Flex justify="space-between" align="center">
        <Typography.Title level={4}>{agentOrders ? <><RobotOutlined /> {agentOrders.title}</> : 'Your orders'}</Typography.Title>
        {agentOrders && <Button onClick={() => set({ agentOrders: null })}>Show all orders</Button>}
      </Flex>
      {!showing ? <Skeleton active /> : (
        <Table rowKey="order_id" dataSource={showing} pagination={{ pageSize: 12 }} size="middle"
          columns={[
            { title: 'Order', dataIndex: 'order_id', render: (id) => <Link to={`/orders/${id}`}>#{id}</Link> },
            { title: 'Placed', dataIndex: 'placed_at', render: (d) => dayjs(d).format('ddd, D MMM YYYY · h:mm A') },
            { title: 'Items', dataIndex: 'summary', ellipsis: true },
            { title: 'Total', dataIndex: 'total', render: (t) => `₹${t}` },
            { title: 'Status', dataIndex: 'status', render: (s) => <Tag color={s === 'delivered' ? 'green' : 'blue'}>{s}</Tag> },
          ]} />
      )}
    </>
  )
}

export function OrderDetail() {
  const { id } = useParams()
  const [o, setO] = useState<Order | null>(null)
  const setCart = useStore((s) => s.setCart)
  const { message } = App.useApp()
  useEffect(() => { setO(null); api.order(Number(id)).then(setO) }, [id])
  if (!o) return <Skeleton active />
  const reorder = async () => {
    for (const it of o.items) { try { setCart(await api.addToCart(it.variant_id, it.quantity)) } catch { /* out of stock */ } }
    message.success('Items added to cart')
  }
  return (
    <>
      <Typography.Title level={4}>Order #{o.order_id}</Typography.Title>
      <Typography.Text type="secondary">{dayjs(o.placed_at).format('dddd, D MMMM YYYY · h:mm A')} · <Tag>{o.status}</Tag>
        {o.delivery && <Tag color="blue">Delivery {o.delivery}</Tag>}</Typography.Text>
      <Card style={{ marginTop: 12 }}>
        <List dataSource={o.items} renderItem={(it, i) => (
          <List.Item extra={<b>₹{(it.price * it.quantity).toFixed(0)}</b>}>
            <List.Item.Meta avatar={<Tag bordered={false}>#{i + 1}</Tag>} title={it.name} description={`${it.label} · ₹${it.price} × ${it.quantity}`} />
          </List.Item>
        )} />
        <Divider />
        <Flex justify="space-between" align="center"><b>Total ₹{o.total}</b><Button onClick={reorder}>Reorder all</Button></Flex>
      </Card>
    </>
  )
}

export function Reminders() {
  const [rows, setRows] = useState<any[] | null>(null)
  const [notes, setNotes] = useState<any[]>([])
  useEffect(() => { api.reminders().then(setRows); api.notifications().then(setNotes) }, [])
  return (
    <Row gutter={20}>
      <Col xs={24} md={12}>
        <Typography.Title level={4}><ClockCircleOutlined /> Reminders</Typography.Title>
        <Typography.Paragraph type="secondary">Created by the assistant as EventBridge Scheduler schedules that publish to SQS.</Typography.Paragraph>
        {rows ? <List bordered dataSource={rows} locale={{ emptyText: 'Try: “remind me every sunday to buy eggs”' }}
          renderItem={(r) => <List.Item extra={<Tag color={r.scheduled ? 'green' : 'orange'}>{r.scheduled ? 'scheduled' : 'pending'}</Tag>}>
            <List.Item.Meta title={r.description} description={<code>{r.schedule}</code>} /></List.Item>} /> : <Skeleton />}
      </Col>
      <Col xs={24} md={12}>
        <Typography.Title level={4}><BellOutlined /> Notifications</Typography.Title>
        <Typography.Paragraph type="secondary">Written by the SQS worker from order and reminder events.</Typography.Paragraph>
        <List bordered dataSource={notes} renderItem={(n) => (
          <List.Item><List.Item.Meta title={n.body} description={dayjs(n.created_at).format('D MMM, h:mm A')} /></List.Item>
        )} />
      </Col>
    </Row>
  )
}
