import { Card, Col, Descriptions, Drawer, Empty, Progress, Row, Skeleton, Statistic, Table, Tag, Timeline, Typography } from 'antd'
import dayjs from 'dayjs'
import { useEffect, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { api } from '../api'

/** Per-turn traces: every LLM call and tool call with latency and tokens. */
export function Traces() {
  const [params, setParams] = useSearchParams()
  const [rows, setRows] = useState<any[] | null>(null)
  const [detail, setDetail] = useState<any | null>(null)
  const open = params.get('id')
  useEffect(() => { api.traces().then(setRows) }, [])
  useEffect(() => { if (open) api.trace(open).then(setDetail); else setDetail(null) }, [open])
  return (
    <>
      <Typography.Title level={4}>Agent traces</Typography.Title>
      {!rows ? <Skeleton active /> : (
        <Table rowKey="id" size="small" dataSource={rows} onRow={(r) => ({ onClick: () => setParams({ id: r.id }) })} rowClassName="clickable"
          columns={[
            { title: 'When', dataIndex: 'created_at', render: (d) => dayjs(d).format('HH:mm:ss'), width: 90 },
            { title: 'Input', dataIndex: 'input', ellipsis: true },
            { title: 'Route', dataIndex: 'route', render: (r) => <Tag color={r === 'fast' ? 'cyan' : 'purple'}>{r}</Tag>, width: 80 },
            { title: 'Tools', dataIndex: 'tools', render: (t: string[]) => t.map((x, i) => <Tag key={i}>{x}</Tag>) },
            { title: 'Latency', dataIndex: 'latency_ms', render: (l) => `${(l / 1000).toFixed(2)}s`, width: 90 },
            { title: 'Tokens', render: (_, r) => `${r.prompt_tokens}/${r.completion_tokens}`, width: 100 },
            { title: '', dataIndex: 'error', render: (e) => e && <Tag color="red">error</Tag>, width: 60 },
          ]} />
      )}
      <Drawer open={!!open} onClose={() => setParams({})} size="large" title={detail?.input}>
        {!detail ? <Skeleton active /> : (
          <>
            <Descriptions size="small" column={2} bordered>
              <Descriptions.Item label="Reply" span={2}>{detail.reply}</Descriptions.Item>
              <Descriptions.Item label="Route">{detail.route}</Descriptions.Item>
              <Descriptions.Item label="Model">{detail.model}</Descriptions.Item>
              <Descriptions.Item label="Latency">{(detail.latency_ms / 1000).toFixed(2)}s</Descriptions.Item>
              <Descriptions.Item label="Tokens">{detail.prompt_tokens} in / {detail.completion_tokens} out</Descriptions.Item>
            </Descriptions>
            <Typography.Title level={5} style={{ marginTop: 16 }}>Steps</Typography.Title>
            <Timeline items={detail.steps.map((s: any) => ({
              color: s.error ? 'red' : s.kind === 'llm' ? 'purple' : s.kind === 'tool' ? 'blue' : 'gray',
              children: (
                <div>
                  <b>{s.kind === 'llm' ? `LLM → ${s.tool_calls?.join(', ') || 'reply'}` : s.kind === 'tool' ? s.tool : s.kind}</b>
                  {s.ms != null && <Tag bordered={false} style={{ marginLeft: 8 }}>{s.ms}ms</Tag>}
                  {s.prompt_tokens != null && <Tag bordered={false}>{s.prompt_tokens}/{s.completion_tokens} tok</Tag>}
                  {s.args && <pre className="code">{JSON.stringify(s.args, null, 1)}</pre>}
                  {s.summary && <div className="muted">{s.summary}</div>}
                  {s.error && <div style={{ color: 'var(--ant-color-error)' }}>{s.error}</div>}
                </div>
              ),
            }))} />
            <Typography.Title level={5}>UI actions</Typography.Title>
            {detail.ui_actions.map((a: any, i: number) => <Tag key={i} color="geekblue">{a.type}{a.payload.path ? ` ${a.payload.path}` : ''}</Tag>)}
          </>
        )}
      </Drawer>
    </>
  )
}

/** Renders the latest eval reports produced by evals/run_agent.py and evals/run_retrieval.py. */
export function Evals() {
  const [data, setData] = useState<any | null>(null)
  useEffect(() => { api.evals().then(setData).catch(() => setData({ agent: [], retrieval: null })) }, [])
  if (!data) return <Skeleton active />
  if (!data.agent.length) return <Empty description="No eval results yet. Run: uv run python -m evals.run_agent" />
  const best = data.agent[0]
  return (
    <>
      <Typography.Title level={4}>Evaluation</Typography.Title>
      <Row gutter={12}>
        <Col xs={12} md={6}><Card><Statistic title="Task success" value={(best.task_success * 100).toFixed(1)} suffix="%" /></Card></Col>
        <Col xs={12} md={6}><Card><Statistic title="Safety pass rate" value={(best.safety_pass_rate * 100).toFixed(0)} suffix="%" /></Card></Col>
        <Col xs={12} md={6}><Card><Statistic title="p50 latency" value={(best.latency_ms.p50 / 1000).toFixed(2)} suffix="s" /></Card></Col>
        <Col xs={12} md={6}><Card><Statistic title="Hallucinated actions" value={(best.hallucinated_action_rate * 100).toFixed(1)} suffix="%" /></Card></Col>
      </Row>
      <Typography.Title level={5} style={{ marginTop: 20 }}>Configurations</Typography.Title>
      <Table rowKey={(r) => r.config.tag} size="small" pagination={false} dataSource={data.agent} scroll={{ x: true }}
        columns={[
          { title: 'Config', render: (_, r) => <b>{r.config.tag}</b> },
          { title: 'Model', render: (_, r) => r.config.model },
          { title: 'Router', render: (_, r) => (r.config.router ? 'on' : 'off') },
          { title: 'Success', render: (_, r) => <Progress percent={+(r.task_success * 100).toFixed(1)} size="small" style={{ width: 120 }} /> },
          { title: 'p50 / p95', render: (_, r) => `${(r.latency_ms.p50 / 1000).toFixed(1)}s / ${(r.latency_ms.p95 / 1000).toFixed(1)}s` },
          { title: 'LLM calls/turn', render: (_, r) => r.llm_calls_per_turn },
          { title: 'Halluc. actions', render: (_, r) => `${(r.hallucinated_action_rate * 100).toFixed(1)}%` },
        ]} />
      <Typography.Title level={5} style={{ marginTop: 20 }}>Success by category ({best.config.tag})</Typography.Title>
      <Row gutter={[12, 12]}>
        {Object.entries(best.by_category).map(([k, v]: any) => (
          <Col key={k} xs={12} md={6}><Card size="small"><div className="muted">{k}</div><Progress percent={+(v * 100).toFixed(0)} /></Card></Col>
        ))}
      </Row>
      {data.retrieval && (
        <>
          <Typography.Title level={5} style={{ marginTop: 20 }}>Retrieval ablation ({data.retrieval.queries} queries)</Typography.Title>
          <Table rowKey="mode" size="small" pagination={false} dataSource={data.retrieval.modes}
            columns={['mode', 'recall@10', 'ndcg@10', 'mrr@10', 'p50_ms', 'p95_ms'].map((k) => ({ title: k, dataIndex: k }))} />
        </>
      )}
    </>
  )
}
