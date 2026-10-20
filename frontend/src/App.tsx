import { BellOutlined, CodeOutlined, ExperimentOutlined, RobotOutlined, ShoppingCartOutlined } from '@ant-design/icons'
import { Badge, Button, Dropdown, Flex, Layout, List, Popover, Select, Tooltip } from 'antd'
import { useEffect, useState } from 'react'
import { Link, Route, Routes, useNavigate } from 'react-router-dom'
import { api, getUserId, setUserId } from './api'
import AssistantPanel from './agent/AssistantPanel'
import CommandBar from './agent/CommandBar'
import ErrorBoundary from './components/ErrorBoundary'
import { CartPage, Checkout, OrderDetail, Orders, Reminders } from './pages/Account'
import { Evals, Traces } from './pages/Dev'
import { Category, Home, ProductPage, Search } from './pages/Shop'
import { useStore } from './store'

const USERS = [{ value: 1, label: 'Aditi (Bengaluru)' }, { value: 2, label: 'Kabir (Delhi)' }, { value: 3, label: 'Sana (Mumbai)' }]

function Notifications() {
  const [items, setItems] = useState<any[]>([])
  const cart = useStore((s) => s.cart)
  useEffect(() => { api.notifications().then(setItems).catch(() => {}) }, [cart])
  return (
    <Popover trigger="click" placement="bottomRight" content={
      <List size="small" style={{ width: 300 }} dataSource={items.slice(0, 6)} locale={{ emptyText: 'No notifications' }}
        renderItem={(n) => <List.Item>{n.body}</List.Item>} />
    }>
      <Badge count={items.length} size="small"><Button type="text" icon={<BellOutlined />} /></Badge>
    </Popover>
  )
}

export default function AppShell() {
  const { cart, setCart, panelOpen, setPanel, resetSession } = useStore()
  const [user, setUser] = useState(getUserId())
  const nav = useNavigate()
  useEffect(() => { api.cart().then(setCart).catch(() => {}) }, [user, setCart])

  return (
    <Layout className="shell">
      <Layout.Header className="header">
        <Link to="/" className="logo"><span className="logo-mark">C</span><span className="logo-text">converse</span></Link>
        <div className="bar-wrap"><CommandBar /></div>
        <Flex align="center" gap={4} className="header-right">
          <Tooltip title="Assistant"><Button type="text" icon={<RobotOutlined />} onClick={() => setPanel(!panelOpen)} /></Tooltip>
          <Notifications />
          <Dropdown menu={{ items: [
            { key: 'traces', icon: <CodeOutlined />, label: 'Agent traces', onClick: () => nav('/dev/traces') },
            { key: 'evals', icon: <ExperimentOutlined />, label: 'Eval results', onClick: () => nav('/dev/evals') },
          ] }}><Button type="text" icon={<CodeOutlined />} /></Dropdown>
          <Select size="small" value={user} options={USERS} className="user-select" variant="borderless"
            onChange={(v) => { setUserId(v); setUser(v); resetSession(); nav('/') }} />
          <Button type="primary" icon={<ShoppingCartOutlined />} onClick={() => nav('/cart')}>
            {cart?.item_count ? `${cart.item_count} · ₹${cart.total}` : 'Cart'}
          </Button>
        </Flex>
      </Layout.Header>
      <Layout hasSider className="main-row">
        <Layout.Content className="content">
          <ErrorBoundary label="This page">
          <Routes>
            <Route path="/" element={<Home />} />
            <Route path="/category/:name" element={<Category />} />
            <Route path="/search" element={<Search />} />
            <Route path="/product/:id" element={<ProductPage />} />
            <Route path="/cart" element={<CartPage />} />
            <Route path="/checkout" element={<Checkout />} />
            <Route path="/orders" element={<Orders />} />
            <Route path="/orders/:id" element={<OrderDetail />} />
            <Route path="/reminders" element={<Reminders />} />
            <Route path="/dev/traces" element={<Traces />} />
            <Route path="/dev/evals" element={<Evals />} />
          </Routes>
          </ErrorBoundary>
        </Layout.Content>
        {panelOpen && <ErrorBoundary label="The assistant"><AssistantPanel /></ErrorBoundary>}
      </Layout>
    </Layout>
  )
}
