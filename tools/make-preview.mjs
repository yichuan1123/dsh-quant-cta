/**
 * 生成面板预览页：把 panel.html 原样拿来，只把 fetch 换成假数据。
 *
 * 用途：没有 DSH 宿主（或宿主还是旧版本）时，也能在真浏览器里检查图表长什么样。
 * 产物写到 .preview/（已 gitignore），不参与发布。
 */

import { mkdirSync, readFileSync, writeFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..')
const html = readFileSync(join(ROOT, 'panel.html'), 'utf8')

/* ── 造一份像样的假数据 ── */
const codes = ['rb', 'hc', 'cu', 'al', 'zn', 'ni', 'ru', 'au', 'ag', 'i', 'j', 'jm', 'm', 'y',
  'p', 'c', 'v', 'TA', 'MA', 'SR', 'CF', 'FG', 'sc', 'IF', 'IC', 'IH', 'IM', 'T']
const sectors = { 黑色: ['rb', 'hc', 'i', 'j', 'jm'], 有色: ['cu', 'al', 'zn', 'ni'], 能化: ['ru', 'v', 'TA', 'MA', 'FG', 'sc'],
  农产品: ['m', 'y', 'p', 'c', 'SR', 'CF'], 贵金属: ['au', 'ag'], 金融: ['IF', 'IC', 'IH', 'IM', 'T'] }

let seed = 7
function rnd() { seed = (seed * 1103515245 + 12345) % 2147483648; return seed / 2147483648 }

const weights = {}
let sum = 0
for (const c of codes) { const w = 0.6 / codes.length + rnd() * 0.7 / codes.length; weights[c] = w; sum += w }
for (const c of codes) weights[c] = Number((weights[c] / sum).toFixed(6))
const risk = {}
for (const c of codes) risk[c] = Number((100 / codes.length).toFixed(2))
const sectorTotals = {}
for (const s of Object.keys(sectors)) {
  sectorTotals[s] = Number((sectors[s].reduce((a, c) => a + weights[c], 0) * 100).toFixed(2))
}

const curve = []
let eq = 1000000
const start = new Date('2024-01-02')
for (let i = 0; i < 260; i++) {
  const d = new Date(start.getTime() + i * 86400000)
  eq *= 1 + (rnd() - 0.47) * 0.012
  curve.push({ date: d.toISOString().slice(0, 10), equity: Number(eq.toFixed(2)), drawdown: 0 })
}
let peak = 0
for (const p of curve) { peak = Math.max(peak, p.equity); p.drawdown = Number(((peak - p.equity) / peak).toFixed(5)) }

const trades = []
for (let i = 0; i < 18; i++) {
  const c = codes[i % codes.length]
  trades.push({ symbol: c, sleeve: 'trend', direction: i % 2 ? 1 : -1, open_date: curve[i * 5].date,
    close_date: curve[i * 5 + 4].date, pnl: Number(((rnd() - 0.42) * 9000).toFixed(2)) })
}

const model = {
  version: 337, asof: curve[curve.length - 1].date, method: 'risk_parity',
  weights, diagnostics: {
    n: codes.length, effective_n: 27.4, ann_vol_pct: 4.45, observations: 250, hhi: 0.0365,
    max_weight_cap: 0.15, sector_cap: 0.35,
    risk_contributions: risk, sector_totals_pct: sectorTotals,
  },
}

const overview = {
  generatedAt: new Date().toISOString(),
  runs: [{ run_id: 'demo_full', data_source: 'synthetic', cagr_pct: 9.46, sharpe: 3.64, max_drawdown_pct: 3.27, trades: 1138 }],
  latest: {
    meta: { run_id: 'demo_full', data_source: 'synthetic', generated_at: new Date().toISOString() },
    metrics: { final_equity: eq, cagr_pct: 9.46, ann_vol_pct: 2.5, sharpe: 3.64, max_drawdown_pct: 3.27,
      profit_factor: 2.5, trades: 1138, cost_total: 116441, total_return_pct: 104.1 },
    sleeves: { trend: { strategy: 'trend', alloc_pct: 60, return_pct: 83.2, sharpe: 1.74, max_drawdown_pct: 5.12, trades: 269 },
      xsec: { strategy: 'xsec', alloc_pct: 40, return_pct: 25.88, sharpe: 0.69, max_drawdown_pct: 13.36, trades: 68 } },
    symbols: { rb: { pnl: 68497, win_rate_pct: 42, trades: 69 }, cu: { pnl: -9769, win_rate_pct: 25, trades: 4 },
      m: { pnl: 210312, win_rate_pct: 57.9, trades: 76 }, TA: { pnl: 154620, win_rate_pct: 66.7, trades: 129 } },
    curve, trades,
  },
  paper: null,
  model,
  sim: { running: false, intervalSec: 300, ticks: 3, last: { at: new Date().toISOString(), last_date: '2026-11-09' }, lastError: '' },
}

const stub = [
  '<script>',
  'window.fetch = function (url, opts) {',
  '  var body;',
  '  if (String(url).indexOf("/api/sim") >= 0) body = window.__PREVIEW__.simFull;',
  '  else if (String(url).indexOf("/api/credentials") >= 0) body = { configured: false, source: null, masked_user: "", path: "C:/preview/credentials.json", file_exists: false, env_present: false };',
  '  else if (String(url).indexOf("/api/report") >= 0) body = window.__PREVIEW__.overview.latest;',
  '  else body = window.__PREVIEW__.overview;',
  '  return Promise.resolve({ ok: true, status: 200, text: function () { return Promise.resolve(JSON.stringify(body)); } });',
  '};',
  'window.__PREVIEW__ = ' + JSON.stringify({ overview, simFull: { running: false, intervalSec: 300, ticks: 3,
    last: { at: new Date().toISOString(), last_date: '2026-11-09', added_days: 1, ok: true, ms: 1200 },
    lastError: '', paper: { last_date: '2026-11-09', data_source: 'synthetic', metrics: { final_equity: eq, total_return_pct: 104.1, max_drawdown_pct: 3.27 },
      positions: codes.slice(0, 6).map(function (c) { return { symbol: c, direction: 1, lots: 3, avg_price: 1000, last_price: 1010 } }), journal: [] }, model } }) + ';',
  '</script>',
].join('\n')

const out = html.replace(/<script>/, stub + '\n<script>')
mkdirSync(join(ROOT, '.preview'), { recursive: true })
writeFileSync(join(ROOT, '.preview', 'chart-preview.html'), out, 'utf8')
console.log('已生成 .preview/chart-preview.html（假数据：' + curve.length + ' 个交易日 / ' + codes.length + ' 个品种）')
