// Headless end-to-end check of the real UI + agent, saving screenshots for the README.
//   node scripts/e2e.mjs            (expects `make api` and `make web` running)
import { chromium } from 'playwright-core'
import { mkdirSync } from 'node:fs'

const BASE = process.env.BASE_URL || 'http://localhost:5173'
const OUT = new URL('../../docs/img/', import.meta.url).pathname
const CHROME = process.env.CHROME_PATH || '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'
mkdirSync(OUT, { recursive: true })

const browser = await chromium.launch({ executablePath: CHROME, headless: true })
const page = await browser.newPage({ viewport: { width: 1440, height: 900 }, colorScheme: 'light' })
const fail = (m) => { console.error('FAIL', m); process.exitCode = 1 }

async function ask(text) {
  const t0 = Date.now()
  await page.fill('.command-bar input', text)
  await page.keyboard.press('Enter')
  // the bar is disabled while the agent works and re-enabled when the turn is done
  await page.waitForFunction(() => !document.querySelector('.command-bar input')?.disabled, null, { timeout: 120_000 })
  await page.waitForTimeout(600)
  console.log(`${((Date.now() - t0) / 1000).toFixed(1)}s  ${text}  ->  ${new URL(page.url()).pathname}`)
}

await page.goto(BASE)
await page.request.delete(`${BASE}/api/cart`, { headers: { 'x-user-id': '1' } })
await page.reload()
await page.waitForSelector('.cat-card')
await page.screenshot({ path: OUT + '01-home.png' })

await ask('show me my purchase of 5th january')
if (!/\/orders\/\d+/.test(page.url())) fail('did not open the 5 Jan order')
await page.screenshot({ path: OUT + '02-order-lookup.png' })

await ask('best pizza ingredients')
if (!page.url().includes('/search')) fail('did not show results')
await page.waitForSelector('.badge-ref')
await page.screenshot({ path: OUT + '03-search.png' })

await ask('add the second one and my usual milk, then take me to checkout')
if (!page.url().endsWith('/checkout')) fail('did not open checkout')
await page.screenshot({ path: OUT + '04-checkout.png' })

await ask('place my order')
await page.waitForSelector('.confirm-card')
await page.screenshot({ path: OUT + '05-confirm.png' })
await page.click('.confirm-card button.ant-btn-primary')
await page.waitForURL(/\/orders\/\d+/, { timeout: 30_000 })
await page.waitForTimeout(800)
await page.screenshot({ path: OUT + '06-order-placed.png' })

await page.goto(BASE + '/dev/traces')
await page.waitForSelector('.ant-table-row')
await page.click('.ant-table-row >> nth=2')
await page.waitForSelector('.ant-timeline')
await page.waitForTimeout(500)
await page.screenshot({ path: OUT + '07-trace.png' })

await page.goto(BASE + '/dev/evals')
await page.waitForTimeout(1500)
await page.screenshot({ path: OUT + '08-evals.png', fullPage: true })

await browser.close()
console.log(process.exitCode ? 'E2E FAILED' : 'E2E OK')
