// Records the LinkedIn/README demo: real browser, real agent, real-time typing. Speed-up happens in ffmpeg.
//   BASE_URL=http://localhost:5180 node scripts/demo-video.mjs
// Output: docs/video/converse-demo-raw.webm (convert with ffmpeg; see docs/video/README in the run log)
import { chromium } from 'playwright-core'
import { mkdirSync, readdirSync, renameSync } from 'node:fs'

const BASE = process.env.BASE_URL || 'http://localhost:5173'
const OUT = new URL('../../docs/video/', import.meta.url).pathname
const CHROME = process.env.CHROME_PATH || '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'
const SIZE = { width: 1440, height: 900 }
mkdirSync(OUT, { recursive: true })

const browser = await chromium.launch({ executablePath: CHROME, headless: true })
const context = await browser.newContext({ viewport: SIZE, colorScheme: 'light', recordVideo: { dir: OUT, size: SIZE } })
const page = await context.newPage()
const pause = (ms) => page.waitForTimeout(ms)

// ---------- overlays: captions, title cards, a visible cursor ----------
async function installOverlays() {
  await page.addStyleTag({ content: `
    #demo-caption { position: fixed; left: 50%; bottom: 28px; transform: translateX(-50%); z-index: 99999;
      background: rgba(17,24,39,.92); color: #fff; font: 600 20px/1.3 Inter, system-ui, sans-serif;
      padding: 12px 22px; border-radius: 12px; box-shadow: 0 8px 30px rgba(0,0,0,.25); max-width: 80vw; text-align: center;
      transition: opacity .3s; }
    #demo-caption small { display:block; font-weight: 500; font-size: 14px; opacity: .75; margin-top: 2px; }
    #demo-card { position: fixed; inset: 0; z-index: 100000; display: flex; flex-direction: column; align-items: center;
      justify-content: center; background: linear-gradient(135deg, #0c831f, #075c15); color: #fff; font-family: Inter, system-ui, sans-serif; }
    #demo-card h1 { font-size: 64px; margin: 0; letter-spacing: -1px; }
    #demo-card p { font-size: 26px; margin: 14px 0 0; opacity: .9; }
    #demo-card .tag { margin-top: 28px; font-size: 18px; padding: 8px 16px; border: 1px solid rgba(255,255,255,.5); border-radius: 999px; }
    #demo-cursor { position: fixed; z-index: 100001; width: 22px; height: 22px; border-radius: 50%; pointer-events: none;
      background: rgba(12,131,31,.35); border: 2px solid #0c831f; transform: translate(-50%,-50%); transition: transform .1s; }
  ` })
  await page.evaluate(() => {
    const c = document.createElement('div'); c.id = 'demo-cursor'; c.style.left = '-50px'; document.body.appendChild(c)
    document.addEventListener('mousemove', (e) => { c.style.left = e.clientX + 'px'; c.style.top = e.clientY + 'px' })
    document.addEventListener('mousedown', () => { c.style.transform = 'translate(-50%,-50%) scale(.7)' })
    document.addEventListener('mouseup', () => { c.style.transform = 'translate(-50%,-50%)' })
  })
}
const caption = (title, sub = '') => page.evaluate(([t, s]) => {
  let el = document.getElementById('demo-caption')
  if (!el) { el = document.createElement('div'); el.id = 'demo-caption'; document.body.appendChild(el) }
  el.innerHTML = t + (s ? `<small>${s}</small>` : '')
}, [title, sub])
const card = (title, sub, tag) => page.evaluate(([t, s, g]) => {
  const el = document.createElement('div'); el.id = 'demo-card'
  el.innerHTML = `<h1>${t}</h1><p>${s}</p>${g ? `<div class="tag">${g}</div>` : ''}`
  document.body.appendChild(el)
}, [title, sub, tag])
const uncard = () => page.evaluate(() => document.getElementById('demo-card')?.remove())

async function ask(text, readMs = 2600) {
  const input = page.locator('.command-bar input')
  await input.click()
  await input.pressSequentially(text, { delay: 45 })
  await pause(300)
  await page.keyboard.press('Enter')
  await page.waitForFunction(() => !document.querySelector('.command-bar input')?.disabled, null, { timeout: 120_000 })
  await pause(readMs)
}

async function clickSmooth(locator) {
  const box = await locator.boundingBox()
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2, { steps: 25 })
  await pause(400)
  await locator.click()
}

// ---------- clean starting state ----------
const H = { 'x-user-id': '1', 'content-type': 'application/json' }
await page.request.delete(`${BASE}/api/cart`, { headers: H })
await page.request.put(`${BASE}/api/delivery-slot`, { headers: H, data: { start: null } })

await page.goto(BASE)
await page.waitForSelector('.cat-card')
await installOverlays()
await page.mouse.move(700, 450)

// ---------- the demo ----------
await card('Converse', 'One input box that can do anything the app can do', 'Local 7B LLM · real responses · shown at 2× speed')
await pause(4000)
await uncard()

await caption('Ask in plain language', 'The agent finds the right order and opens it')
await ask('show me my purchase of 5th january', 3000)

await caption('Hybrid search: keywords + meaning + reranker')
await ask('best pizza ingredients', 2600)

await caption('It remembers what is on screen', '“the second one” → item #2 in the results')
await ask('add the second one', 2400)

await caption('Several actions in one request', 'Typos are fine · 5 AM is outside delivery hours, so it books 6–7 AM and says so')
await ask('add farmfresh tomatoes and check out tomorrow around 5 am', 4200)

await caption('Safety is built in: the AI can propose an order, only your click can place it')
await ask('place my order', 1800)
await clickSmooth(page.locator('.confirm-card button.ant-btn-primary'))
await page.waitForURL(/\/orders\/\d+/, { timeout: 30_000 })
await pause(2600)

await caption('Every turn is traced: each LLM call and tool call, with timings')
await page.goto(BASE + '/dev/traces')
await installOverlays()
await caption('Every turn is traced: each LLM call and tool call, with timings')
await page.waitForSelector('.ant-table-row')
await page.mouse.move(700, 450)
await pause(800)
await clickSmooth(page.locator('.ant-table-row', { hasText: 'tomatoes' }).first())
await page.waitForSelector('.ant-timeline')
await pause(3600)

await page.goto(BASE + '/dev/evals')
await installOverlays()
await caption('Measured, not guessed: 130-task eval suite', '74% → 93% task success · 100% on safety tasks · fine-tuned 1.5B matches a 3B')
await pause(2500)
for (let y = 0; y < 6; y++) { await page.mouse.wheel(0, 160); await pause(450) }
await pause(2200)

await card('github.com/R4255/converse', 'Agent · evals · hybrid search · LoRA fine-tuning', 'Built by Rohit Gupta')
await pause(4500)

await context.close()
await browser.close()
const webm = readdirSync(OUT).filter((f) => f.endsWith('.webm') && !f.startsWith('converse-')).map((f) => OUT + f)
renameSync(webm[webm.length - 1], OUT + 'converse-demo-raw.webm')
console.log('recorded', OUT + 'converse-demo-raw.webm')
