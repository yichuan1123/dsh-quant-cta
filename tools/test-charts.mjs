/**
 * 面板图表的无浏览器测试。
 *
 * 做法：从 panel.html 抽出那段 <script>，在 vm 沙箱里用**假 DOM** 跑一遍，
 * 然后直接调图表函数，断言生成的 SVG 节点。
 *
 * 这样测的是**真正发布的那份代码**，不是复制品——图表改了、测试不会悄悄失配。
 */

import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import vm from 'node:vm'

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..')
const html = readFileSync(join(ROOT, 'panel.html'), 'utf8')
const m = html.match(/<script>([\s\S]*?)<\/script>/)
if (!m) { console.error('panel.html 里找不到 <script>'); process.exit(1) }
const source = m[1]

/* ── 假 DOM ── */
function node(tag) {
  const n = {
    tagName: tag, attrs: {}, children: [], style: {}, className: '', textContent: '',
    setAttribute(k, v) { n.attrs[k] = v },
    getAttribute(k) { return n.attrs[k] },
    removeAttribute(k) { delete n.attrs[k] },
    appendChild(c) { n.children.push(c); c.parent = n; return c },
    removeChild(c) { n.children = n.children.filter((x) => x !== c); return c },
    addEventListener() {},
    querySelector() { return null },
    getTotalLength() { return 120 },
    getBoundingClientRect() { return { left: 0, top: 0, width: 660, height: 230 } },
  }
  Object.defineProperty(n, 'firstChild', { get() { return n.children[0] || null } })
  return n
}

const ids = ['refresh', 'runs', 'src', 'runid', 'warn', 'cards', 'chart', 'chart2', 'chartnote',
  'sleeves', 'symbols', 'trades', 'paper', 'simstatus', 'simmsg', 'simadvance', 'simstart', 'simstop',
  'model', 'constraints', 'donut', 'donutlegend', 'sectorbar', 'intraday', 'intradaytitle',
  'intraday-returns', 'intraday-equity', 'credstatus', 'creduser', 'credpass', 'credsave',
  'credtest', 'credclear', 'credmsg']
const registry = {}
for (const id of ids) registry[id] = node('div')

const sandbox = {
  console,
  document: {
    createElement: (t) => node(t),
    createElementNS: (_ns, t) => node(t),
    getElementById: (id) => registry[id] || node('div'),
    querySelectorAll: () => [],        // 区间按钮：测试里不绑也行
    querySelector: () => null,
  },
  localStorage: undefined,             // 参数持久化在测试环境里降级为内存默认值
  fetch: () => new Promise(() => {}),   // 永远 pending：只验证图表，不触发网络
  setTimeout,
  confirm: () => false,
}
sandbox.window = sandbox
vm.createContext(sandbox)
vm.runInContext(source, sandbox, { filename: 'panel-inline.js' })

let failures = 0
function assert(cond, msg) {
  if (!cond) { console.error('FAIL: ' + msg); failures += 1; return }
  console.log('ok   ' + msg)
}
function all(node_, tag) {
  const out = []
  const walk = (n) => { for (const c of n.children || []) { if (!tag || c.tagName === tag) out.push(c); walk(c) } }
  walk(node_)
  return out
}

const C = sandbox.window.__dshQuantCtaCharts
assert(C !== undefined, '图表函数已暴露到 window.__dshQuantCtaCharts')

/* ── 1. 纯函数 ── */
const ticks = C.niceTicks(0, 100, 4)
assert(Array.isArray(ticks) && ticks.length >= 2, 'niceTicks 返回至少两个刻度')
assert(ticks[0] <= 100 && ticks[ticks.length - 1] >= 0, '刻度落在区间内')
assert(ticks.every((v, i) => i === 0 || v > ticks[i - 1]), '刻度严格递增')

const cm = C.colorMap(['rb', 'cu', 'm', 'TA', 'au'])
const colors = Object.values(cm)
assert(new Set(colors).size === colors.length, '每个品种颜色互不相同（' + colors.length + ' 个）')
assert(C.colorMap(['rb', 'cu', 'm'])['rb'] === cm['rb'], '同一品种颜色稳定（不随集合增减变化）')
const many = C.colorMap(['rb', 'hc', 'cu', 'al', 'zn', 'ni', 'ru', 'au', 'ag', 'i', 'j', 'jm',
  'm', 'y', 'p', 'c', 'v', 'TA', 'MA', 'SR', 'CF', 'FG', 'sc', 'IF', 'IC', 'IH', 'IM', 'T'])
const manyColors = Object.values(many)
assert(new Set(manyColors).size === 28, '全盘 28 个品种颜色两两不同（实际 ' + new Set(manyColors).size + '）')

assert(String(C.constraintsText({ diagnostics: {}, weights: {} })).length > 0, '无权重时 constraintsText 不崩')
const ct = C.constraintsText({
  diagnostics: { max_weight_cap: 0.15, sector_cap: 0.35, sector_totals_pct: { '黑色': 40, '有色': 30 } },
  weights: { rb: 0.15, cu: 0.1, m: 0.2 },
})
assert(ct.includes('15.0%') && ct.includes('超限板块'), '约束文案包含上限与超限提示')

/* ── 2. 折线图 ── */
const host1 = node('div')
const pts = []
for (let i = 0; i < 60; i++) pts.push({ x: '2024-01-' + String(1 + (i % 28)).padStart(2, '0'), y: 1000000 + i * 1000 })
C.drawLine(host1, { points: pts, color: '#5b8cff', height: 240, threshold: -20, unit: '%' })
const svg1 = host1.children[0]
assert(svg1 && svg1.tagName === 'svg', '折线图生成了 svg')
const paths1 = all(svg1, 'path')
assert(paths1.length >= 2, '折线图至少两条 path（面积 + 线）')
assert(/^M[\d.]+ [\d.]+ L/.test(paths1[paths1.length - 1].attrs.d || paths1[1].attrs.d || ''), '折线 path 是折线段')
assert(all(svg1, 'line').some((l) => l.attrs.class === 'halt-line'), '熔断阈值参考线存在')
assert(all(svg1, 'text').length >= 4, '存在轴标签文本')

/* ── 3. 环形饼图 ── */
const host2 = node('div')
C.drawDonut(host2, {
  items: [
    { label: 'rb', value: 0.2, color: 'hsl(10,62%,60%)', capped: true },
    { label: 'cu', value: 0.3, color: 'hsl(140,62%,60%)' },
    { label: 'm', value: 0.5, color: 'hsl(260,62%,60%)' },
  ],
  cap: 0.15,
})
const svg2 = host2.children[0]
const arcs = all(svg2, 'path')
assert(arcs.length === 3, '饼图扇区数 = 品种数（' + arcs.length + '）')
assert(arcs.every((a) => String(a.attrs.d).includes('A')), '每个扇区都是圆弧路径')
assert(arcs.some((a) => a.attrs['data-code'] === 'rb' && a.attrs.stroke !== 'rgba(0,0,0,.35)'), '顶格品种用警示色描边')
assert(all(svg2, 'text').some((t) => t.textContent === '3'), '圆心显示品种数')

/* ── 4. 板块条 ── */
const host3 = node('div')
C.drawSectorBars(host3, { items: [{ label: '黑色', value: 0.42 }, { label: '有色', value: 0.2 }], cap: 0.35 })
const svg3 = host3.children[0]
assert(all(svg3, 'rect').length >= 4, '板块条每行两个 rect（底 + 值）')
assert(all(svg3, 'line').some((l) => l.attrs.class === 'cap-line'), '板块上限参考线存在')
assert(all(svg3, 'text').some((t) => String(t.textContent).includes('!')), '超限板块标了感叹号')

/* ── 5. 逐日收益柱 ── */
const host4 = node('div')
const bars = []
for (let i = 0; i < 40; i++) bars.push({ label: '2024-02-' + String(1 + (i % 28)).padStart(2, '0'), value: i % 3 === 0 ? -0.8 : 0.6 })
C.drawBars(host4, { items: bars, height: 210, cumulative: true })
const svg4 = host4.children[0]
const rects = all(svg4, 'rect').filter((r) => r.attrs['data-date'])
assert(rects.length === bars.length, '柱数与交易日数一致（' + rects.length + '）')
assert(rects.some((r) => r.attrs.fill === '#29c08a') && rects.some((r) => r.attrs.fill === '#e5533d'),
  '正收益绿、负收益红')
assert(all(svg4, 'path').length >= 1, '累计虚线存在')

/* ── 6. 空数据不抛错 ── */
const host5 = node('div')
C.drawLine(host5, { points: [] })
assert(all(host5, 'text').length === 1, '空数据折线图给出占位文本')

/* ── 7. 降采样：保形 ── */
const series = []
for (let i = 0; i < 2000; i++) {
  let y = 1000000 + i * 10
  if (i === 1200) y += 180000        // 一个尖峰，降采样不许把它抹掉
  series.push({ x: '2024-01-01', y })
}
const sampled = C.lttb(series, 300)
assert(sampled.length <= 302, 'LTTB 把 2000 点压到 ~300 点（实际 ' + sampled.length + '）')
assert(sampled[0] === series[0] && sampled[sampled.length - 1] === series[series.length - 1], 'LTTB 保留首尾点')
assert(Math.max.apply(null, sampled.map((p) => p.y)) >= 1000000 + 1200 * 10 + 170000, 'LTTB 保住了尖峰（不被抹平）')

/* ── 8. 时间刻度：跨年标年份 ── */
const multiYear = []
for (let y = 2019; y <= 2026; y++) for (let m = 1; m <= 3; m++) multiYear.push({ x: y + '-0' + m + '-01', y: 1 })
const yTicks = C.timeTicks(multiYear, 6)
assert(yTicks.length >= 2 && yTicks.every((t) => String(t.label).length === 4), '跨年时刻度是年份标签')

/* ── 9. 滚动波动率 ── */
const rets = []
for (let i = 0; i < 60; i++) rets.push({ label: '2024-01-' + String(1 + (i % 28)).padStart(2, '0'), value: (i % 2 ? 1 : -1) * 0.5 })
const vol = C.rollingVol(rets, 20)
assert(vol.length === 60 && vol[0].y === 0 && vol[19].y === 0, '滚动波动率预热期为 0')
assert(vol[40].y > 0, '预热后波动率为正（' + vol[40].y.toFixed(2) + '%）')

/* ── 10. 叠加线与副轴 ── */
const host6 = node('div')
const ovPts = pts.map((p, i) => ({ x: p.x, y: 1000000 + i * 900 }))
const secPts = pts.map((p) => ({ x: p.x, y: 8 + Math.random() }))
C.drawLine(host6, {
  points: pts, color: '#5b8cff', height: 240,
  overlay: [{ points: ovPts, color: '#f0b429', label: '期望路径 10%' }],
  secondary: { points: secPts, unit: '%', label: '波动' },
})
const svg6 = host6.children[0]
const dashed = all(svg6, 'path').filter((p) => String(p.attrs['stroke-dasharray'] || '').includes('5'))
assert(dashed.length >= 1, '期望路径画成虚线叠加')
assert(all(svg6, 'text').some((t) => String(t.textContent).indexOf('期望路径') >= 0), '叠加线有标签')
assert(all(svg6, 'text').some((t) => String(t.textContent).indexOf('波动') >= 0), '副轴有标签')
const dots = all(svg6, 'circle')
assert(dots.length >= 3, '画了极值/末值标记（' + dots.length + ' 个点）')

if (failures > 0) {
  console.error('\n' + failures + ' 项失败')
  process.exit(1)
}
console.log('\n图表测试全部通过。')
