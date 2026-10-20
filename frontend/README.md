# Converse frontend

React + TypeScript + Vite + Ant Design. The assistant drives the UI: agent turns stream over SSE
(`src/api.ts`), and typed `ui_actions` are executed in `src/agent/useAgent.ts`.

```bash
npm install
npm run dev          # http://localhost:5173 (proxies /api to :8000)
node scripts/e2e.mjs # headless end-to-end run + screenshots (needs Chrome)
```
