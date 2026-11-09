/**
 * dsh-quant-cta — 宿主端（Host half）
 *
 * 职责：把 qcta_* 工具暴露给模型，并在 Web 服务上挂一个只读面板路由。
 * 真正干活的是插件目录下 python/qcta（回测引擎、成本模型、风控、模拟盘账本）。
 *
 * 刻意不 import 任何 @deepseek-ai/*：profile 安装的插件从自己的目录解析模块，
 * harness 的包在那里不可达。一切走 ctx 和 node 内置模块。
 */

import { spawn } from 'node:child_process'
import { existsSync, readFileSync, readdirSync } from 'node:fs'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

export const name = 'dsh-quant-cta'

/** cordis 注入的插件配置（pythonPath / engineRoot），apply 时填充。 */
let CONFIG = null

const HERE = dirname(fileURLToPath(import.meta.url))
const PKG_ROOT = resolve(HERE, '..')

const DEFAULT_TIMEOUT_MS = 240_000

/* ─────────────────────── 路径与 Python 解析 ─────────────────────── */

function engineRootOf(config) {
  if (config !== null && config !== undefined && typeof config.engineRoot === 'string' && config.engineRoot.trim() !== '') {
    return resolve(config.engineRoot)
  }
  return PKG_ROOT
}

function pythonCandidates(config) {
  const out = []
  if (config !== null && config !== undefined && typeof config.pythonPath === 'string' && config.pythonPath.trim() !== '') {
    out.push({ exe: config.pythonPath, label: 'config.pythonPath' })
  }
  if (typeof process.env.QCTA_PYTHON === 'string' && process.env.QCTA_PYTHON !== '') {
    out.push({ exe: process.env.QCTA_PYTHON, label: 'QCTA_PYTHON' })
  }
  const root = engineRootOf(config)
  const rel = process.platform === 'win32' ? ['Scripts', 'python.exe'] : ['bin', 'python']
  out.push({ exe: join(root, '.venv', ...rel), label: '.venv' })
  out.push({ exe: 'python', label: 'PATH' })
  return out
}

function pickPython(config) {
  const cands = pythonCandidates(config)
  for (const c of cands) {
    if (c.exe === 'python') return c
    try {
      if (existsSync(c.exe)) return c
    } catch { /* ignore */ }
  }
  return { exe: 'python', label: 'PATH' }
}

/* ─────────────────────── 跑引擎 ─────────────────────── */

function runEngine(config, argv, timeoutMs) {
  return new Promise((resolvePromise) => {
    const root = engineRootOf(config)
    const py = pickPython(config)
    const workdir = join(root, 'python')
    if (!existsSync(workdir)) {
      resolvePromise({ ok: false, code: -1, stdout: '', stderr: '找不到引擎目录：' + workdir + '（插件安装不完整？）' })
      return
    }
    const child = spawn(py.exe, ['-m', 'qcta', ...argv], {
      cwd: workdir,
      windowsHide: true,
      env: { ...process.env, PYTHONIOENCODING: 'utf-8' },
    })
    let out = ''
    let err = ''
    let done = false
    const timer = setTimeout(() => {
      if (!done) {
        done = true
        try { child.kill() } catch { /* ignore */ }
        resolvePromise({ ok: false, code: -2, stdout: out, stderr: '执行超时（' + Math.round((timeoutMs || DEFAULT_TIMEOUT_MS) / 1000) + 's）：' + err.slice(-400) })
      }
    }, timeoutMs || DEFAULT_TIMEOUT_MS)
    child.stdout.on('data', (c) => { out += String(c) })
    child.stderr.on('data', (c) => { err += String(c) })
    child.on('error', (e) => {
      if (done) return
      done = true
      clearTimeout(timer)
      resolvePromise({ ok: false, code: -3, stdout: out, stderr: '无法启动 Python（' + py.exe + '）：' + String(e && e.message) })
    })
    child.on('close', (code) => {
      if (done) return
      done = true
      clearTimeout(timer)
      resolvePromise({ ok: code === 0, code, stdout: out, stderr: err })
    })
  })
}

function parseJson(text) {
  const s = String(text || '').trim()
  if (s === '') return null
  try { return JSON.parse(s) } catch { /* 继续找 */ }
  const i = s.indexOf('{')
  const j = s.lastIndexOf('}')
  if (i >= 0 && j > i) {
    try { return JSON.parse(s.slice(i, j + 1)) } catch { return null }
  }
  return null
}

async function callEngine(config, argv, timeoutMs) {
  const res = await runEngine(config, argv, timeoutMs)
  const data = parseJson(res.stdout)
  if (data === null) {
    const tail = (res.stderr || res.stdout || '').slice(-800)
    return { engineOk: false, data: null, error: '引擎没有返回 JSON（exit=' + res.code + '）\n' + tail }
  }
  return { engineOk: res.ok, data, error: res.ok ? '' : ((res.stderr || '').slice(-600)) }
}

/* ─────────────────────── 只读数据（面板与报告） ─────────────────────── */

function runsDirOf(config) {
  return join(engineRootOf(config), 'runs')
}

function listRuns(config, limit) {
  const base = runsDirOf(config)
  if (!existsSync(base)) return []
  let names = []
  try { names = readdirSync(base) } catch { return [] }
  const rows = []
  for (const n of names) {
    const p = join(base, n, 'report.json')
    if (!existsSync(p)) continue
    try {
      const d = JSON.parse(readFileSync(p, 'utf8'))
      const m = d.metrics || {}
      rows.push({
        run_id: n,
        path: join(base, n),
        generated_at: (d.meta || {}).generated_at || '',
        data_source: (d.meta || {}).data_source || '',
        cagr_pct: m.cagr_pct,
        sharpe: m.sharpe,
        max_drawdown_pct: m.max_drawdown_pct,
        trades: m.trades,
      })
    } catch { /* 跳过坏文件 */ }
  }
  rows.sort((a, b) => String(b.run_id).localeCompare(String(a.run_id)))
  return typeof limit === 'number' && limit > 0 ? rows.slice(0, limit) : rows
}

function readRun(config, runId) {
  const base = runsDirOf(config)
  let target = runId
  if (!target) {
    const rows = listRuns(config, 1)
    if (rows.length === 0) return null
    target = rows[0].run_id
  }
  const p = join(base, target, 'report.json')
  if (!existsSync(p)) return null
  try { return JSON.parse(readFileSync(p, 'utf8')) } catch { return null }
}

function readPaper(config) {
  const p = join(engineRootOf(config), 'state', 'paper.json')
  if (!existsSync(p)) return null
  try { return JSON.parse(readFileSync(p, 'utf8')) } catch { return null }
}

/* ─────────────────────── 工具定义 ─────────────────────── */

const TEXT_OUT = {
  schema: { type: 'object' },
  render: (_args, value) => [{ type: 'text', text: String(value && value.text ? value.text : '') }],
}

function fail(text) {
  return { ok: false, text }
}

const TOOL_DOCTOR = {
  name: 'qcta_doctor',
  description: [
    '量化 CTA 引擎的环境自检：Python 版本、tqsdk 是否安装、快期账号凭据、行情缓存与运行目录。',
    '在跑任何回测之前先调它一次，能立刻看出「有没有真实行情」还是「只能跑合成数据」。',
  ].join('\n'),
  parameters: { type: 'object', properties: {} },
  output: TEXT_OUT,
  async execute() {
    const r = await callEngine(CONFIG, ['doctor', '--json'])
    if (r.data === null) return fail(r.error)
    const d = r.data
    const lines = d.checks.map((c) => (c.ok ? 'OK   ' : 'MISS ') + c.name + '  ' + c.detail)
    lines.push('')
    if (!d.credentials) {
      lines.push('注意：没有快期账号 → 回测会用确定性合成数据，报告里标注 synthetic，不能当真实行情结论。')
    }
    if (!d.tqsdk) {
      lines.push('提示：pip install tqsdk 之后才能拉真实行情；数据与撮合模型的代码路径不依赖它。')
    }
    return { ok: true, text: lines.join('\n'), data: d }
  },
}

const TOOL_DATA = {
  name: 'qcta_data',
  description: [
    '行情数据管理。action="status" 看本地缓存覆盖；action="sync" 同步数据。',
    'sync 带 live=true 时从天勤（TqSdk）拉真实主连日线；没有快期凭据会明确报错，不会静默伪造。',
  ].join('\n'),
  parameters: {
    type: 'object',
    properties: {
      action: { type: 'string', enum: ['status', 'sync'], description: '默认 status' },
      pool: { type: 'string', description: 'starter / default / 逗号分隔代码，默认 default' },
      live: { type: 'boolean', description: 'sync 时是否用天勤真实行情' },
    },
  },
  output: TEXT_OUT,
  async execute(args) {
    const a = args || {}
    const action = a.action === 'sync' ? 'sync' : 'status'
    const argv = ['data', action, '--json']
    if (a.pool) argv.push('--pool', String(a.pool))
    if (a.live === true) argv.push('--live')
    const r = await callEngine(CONFIG, argv)
    if (r.data === null) return fail(r.error)
    const d = r.data
    if (action === 'status') {
      const rows = (d.symbols || []).filter((s) => s.bars > 0)
      const lines = ['缓存目录：' + d.cache_dir, '已缓存 ' + rows.length + ' 个品种：']
      for (const s of rows.slice(0, 30)) lines.push('  ' + s.code.padEnd(4) + ' ' + String(s.bars).padStart(5) + ' 根  ' + s.first + ' → ' + s.last)
      if (rows.length === 0) lines.push('  （空——先跑一次 qcta_data action="sync"）')
      return { ok: true, text: lines.join('\n'), data: d }
    }
    const lines = ['同步结果：']
    for (const s of d.synced || []) {
      lines.push('  ' + String(s.code).padEnd(4) + ' ' + String(s.source) + (s.error ? '  ' + String(s.error).slice(0, 160) : '  ' + String(s.bars) + ' 根'))
    }
    return { ok: true, text: lines.join('\n'), data: d }
  },
}

const TOOL_BACKTEST = {
  name: 'qcta_backtest',
  description: [
    '跑一次完整流程的商品期货 CTA 回测，产出报告（markdown / json / svg）到 runs/<run_id>/。',
    '',
    '回测口径（写死在引擎里，不是可选项）：信号在 t 日收盘产生、t+1 日开盘成交；',
    '手数按 ATR 波动率预算换算；扣手续费与滑点；带保证金校验、杠杆上限、回撤熔断、ATR 止损。',
    '',
    '默认品种池 starter（6 个品种），默认两条策略腿 trend(60%) + xsec(40%)。',
    'live=true 会用天勤真实行情；没有快期凭据时自动回落合成数据，并在报告顶部显著标注。',
    '返回 run_id，之后用 qcta_report 读详细报告。',
  ].join('\n'),
  parameters: {
    type: 'object',
    properties: {
      pool: { type: 'string', description: 'starter / default / 逗号分隔代码（默认 default）' },
      start: { type: 'string', description: '开始日期 YYYY-MM-DD，默认 2018-01-01' },
      end: { type: 'string', description: '结束日期，空=到最新' },
      capital: { type: 'number', description: '初始资金，默认 1000000' },
      vol: { type: 'number', description: '组合目标年化波动，默认 0.10' },
      sleeves: { type: 'string', description: '策略腿与资金占比，如 trend:0.6,xsec:0.4' },
      slip: { type: 'number', description: '每笔滑点（跳），默认 1' },
      leverage: { type: 'number', description: '名义杠杆上限，默认 1.5' },
      dd_halt: { type: 'number', description: '回撤熔断阈值，默认 0.20' },
      live: { type: 'boolean', description: '是否用天勤真实行情' },
      refresh: { type: 'boolean', description: '是否忽略本地缓存重新拉取' },
      name: { type: 'string', description: '自定义 run_id' },
    },
  },
  output: TEXT_OUT,
  async execute(args) {
    const a = args || {}
    const argv = ['backtest', '--json']
    const map = { pool: '--pool', start: '--start', end: '--end', capital: '--capital', vol: '--vol',
      sleeves: '--sleeves', slip: '--slip', leverage: '--leverage', dd_halt: '--dd-halt', name: '--name' }
    for (const k of Object.keys(map)) {
      if (a[k] !== undefined && a[k] !== null && a[k] !== '') argv.push(map[k], String(a[k]))
    }
    if (a.live === true) argv.push('--live')
    if (a.refresh === true) argv.push('--refresh')
    const r = await callEngine(CONFIG, argv)
    if (r.data === null) return fail(r.error)
    const d = r.data
    if (d.ok === false) return fail('回测失败：' + (d.error || r.error))
    const m = d.metrics || {}
    const lines = [
      '回测完成 run_id=' + d.run_id,
      '数据来源：' + d.data_source + (d.data_source === 'synthetic' ? '（合成数据，仅验证流程）' : ''),
      '区间 ' + m.start + ' → ' + m.end + '（' + m.trading_days + ' 交易日）',
      '总收益 ' + fmtPct(m.total_return_pct) + ' / 年化 ' + fmtPct(m.cagr_pct) + ' / 波动 ' + fmtPct(m.ann_vol_pct),
      '夏普 ' + fmtNum(m.sharpe, 2) + ' / 最大回撤 ' + fmtPct(m.max_drawdown_pct) + ' / 卡玛 ' + fmtNum(m.calmar, 2),
      '交易 ' + m.trades + ' 笔 / 胜率 ' + fmtPct(m.win_rate_pct) + ' / PF ' + fmtNum(m.profit_factor, 2),
      '成本 ' + fmtMoney(m.cost_total) + ' 元（占初始 ' + fmtPct(m.cost_pct_initial) + '）',
      '',
      '报告：' + d.report_md,
      '面板：/dsh-quant-cta/panel',
    ]
    if ((d.notes || []).length > 0) lines.push('', '数据说明：' + d.notes.slice(0, 4).join('；'))
    return { ok: true, text: lines.join('\n'), run_id: d.run_id, metrics: m, data_source: d.data_source, data: d }
  },
}

const TOOL_REPORT = {
  name: 'qcta_report',
  description: [
    '读取回测报告。action="list" 列出历史回测；action="get" 打印某次回测的完整 markdown 报告（默认最近一次）；',
    'action="metrics" 只返回结构化指标 JSON（省 token）。',
  ].join('\n'),
  parameters: {
    type: 'object',
    properties: {
      action: { type: 'string', enum: ['list', 'get', 'metrics'], description: '默认 get' },
      run_id: { type: 'string', description: '回测 id，空=最近一次' },
    },
  },
  output: TEXT_OUT,
  async execute(args) {
    const a = args || {}
    const action = a.action || 'get'
    if (action === 'list') {
      const rows = listRuns(CONFIG, 30)
      if (rows.length === 0) return { ok: true, text: '还没有回测记录，先跑 qcta_backtest。', data: [] }
      const lines = ['历史回测（新→旧）：']
      for (const r of rows) {
        lines.push('  ' + r.run_id.padEnd(28) + ' ' + String(r.data_source).padEnd(9) +
          ' CAGR ' + fmtPct(r.cagr_pct) + ' 夏普 ' + fmtNum(r.sharpe, 2) + ' 回撤 ' + fmtPct(r.max_drawdown_pct))
      }
      return { ok: true, text: lines.join('\n'), data: rows }
    }
    const argv = ['report', '--json']
    if (a.run_id) argv.push('--run', String(a.run_id))
    const r = await callEngine(CONFIG, argv)
    if (r.data === null) return fail(r.error)
    const d = r.data
    if (action === 'metrics') return { ok: true, text: JSON.stringify(d.metrics || {}, null, 2), data: d.metrics }
    // get 读回真正生成过的 markdown，避免「描述说打印全文、实际只给摘要」。
    const runId = a.run_id || (d.meta && d.meta.run_id) || ''
    const mdPath = join(runsDirOf(CONFIG), runId, 'report.md')
    try {
      const md = readFileSync(mdPath, 'utf8')
      const cap = 12000
      const body = md.length > cap ? md.slice(0, cap) + '\n\n…（截断，完整报告见 ' + mdPath + '）' : md
      return { ok: true, text: body, data: d }
    } catch { /* 文件缺失时退回摘要 */ }

    const m = d.metrics || {}
    const lines = ['# ' + (d.meta && d.meta.run_id ? d.meta.run_id : 'latest'), '']
    lines.push('数据来源：' + ((d.meta || {}).data_source || ''))
    lines.push('CAGR ' + fmtPct(m.cagr_pct) + ' / 波动 ' + fmtPct(m.ann_vol_pct) + ' / 夏普 ' + fmtNum(m.sharpe, 2) +
      ' / 回撤 ' + fmtPct(m.max_drawdown_pct) + ' / PF ' + fmtNum(m.profit_factor, 2))
    lines.push('')
    lines.push('策略腿：')
    for (const k of Object.keys(d.sleeves || {})) {
      const s = d.sleeves[k]
      lines.push('  ' + k.padEnd(8) + ' ' + String(s.strategy).padEnd(6) + ' 收益 ' + fmtPct(s.return_pct) +
        ' 夏普 ' + fmtNum(s.sharpe, 2) + ' 回撤 ' + fmtPct(s.max_drawdown_pct) + ' 交易 ' + s.trades)
    }
    lines.push('品种归因（净盈亏）：')
    for (const k of Object.keys(d.symbols || {})) {
      const s = d.symbols[k]
      lines.push('  ' + k.padEnd(4) + ' ' + fmtMoney(s.pnl).padStart(12) + ' 胜率 ' + fmtPct(s.win_rate_pct) + ' 交易 ' + s.trades)
    }
    lines.push('')
    lines.push('完整 markdown 在 ' + join(runsDirOf(CONFIG), a.run_id || (d.meta && d.meta.run_id) || '', 'report.md'))
    return { ok: true, text: lines.join('\n'), data: d }
  },
}

const TOOL_PAPER = {
  name: 'qcta_paper',
  description: [
    '模拟盘（paper trading）。action: init / step / status / reset。',
    '',
    '分两步走：init 建立会话（品种池、资金、策略腿），step 把账本推进到最新交易日。',
    'step 是幂等的：同一天重复调用不会产生重复成交记录。',
    '明确边界：本插件不发任何真实委托，也没有下单代码路径；paper 的账本是「用真实行情逐日推进的确定性结果」，',
    '它能验证全链路与数据管道，但拿不到真实盘中滑点与流动性冲击。',
  ].join('\n'),
  parameters: {
    type: 'object',
    properties: {
      action: { type: 'string', enum: ['init', 'step', 'status', 'reset'], description: '默认 status' },
      pool: { type: 'string', description: 'init 用：starter / default / 代码列表，默认 starter' },
      capital: { type: 'number', description: 'init 用：初始资金，默认 300000' },
      vol: { type: 'number', description: 'init 用：目标年化波动，默认 0.10' },
      sleeves: { type: 'string', description: 'init 用：策略腿，默认 trend:0.7,xsec:0.3' },
      start: { type: 'string', description: 'init 用：回放起点，默认 2019-01-01' },
      live: { type: 'boolean', description: 'step 用：是否用天勤真实行情' },
    },
  },
  output: TEXT_OUT,
  async execute(args) {
    const a = args || {}
    const action = a.action || 'status'
    if (action === 'init') {
      const argv = ['paper', 'init', '--json']
      if (a.pool) argv.push('--pool', String(a.pool))
      if (a.capital) argv.push('--capital', String(a.capital))
      if (a.vol) argv.push('--vol', String(a.vol))
      if (a.sleeves) argv.push('--sleeves', String(a.sleeves))
      if (a.start) argv.push('--start', String(a.start))
      const r = await callEngine(CONFIG, argv)
      if (r.data === null) return fail(r.error)
      const d = r.data
      return { ok: true, text: 'paper 会话已建立：' + (d.codes || []).join(', ') + '\n状态文件：' + d.state_path + '\n下一步：qcta_paper action="step"', data: d }
    }
    if (action === 'reset') {
      const r = await callEngine(CONFIG, ['paper', 'reset', '--json'])
      if (r.data === null) return fail(r.error)
      return { ok: true, text: 'paper 状态已重置（旧文件存为 .bak）', data: r.data }
    }
    if (action === 'step') {
      const argv = ['paper', 'step', '--json']
      if (a.live === true) argv.push('--live')
      const r = await callEngine(CONFIG, argv)
      if (r.data === null) return fail(r.error)
      const d = r.data
      if (d.ok === false) return fail('推进失败：' + (d.error || ''))
      const m = d.metrics || {}
      const lines = [
        'paper 已推进到 ' + d.last_date + '（新增 ' + d.added_days + ' 个交易日）',
        '权益 ' + fmtMoney(m.final_equity) + ' / 总收益 ' + fmtPct(m.total_return_pct) + ' / 回撤 ' + fmtPct(m.max_drawdown_pct),
        '当前持仓 ' + (d.positions || []).length + ' 个',
      ]
      for (const p of d.positions || []) {
        lines.push('  ' + String(p.symbol).padEnd(4) + (p.direction > 0 ? ' 多 ' : ' 空 ') + p.lots + ' 手 @ ' + p.avg_price + '（现 ' + p.last_price + '）')
      }
      return { ok: true, text: lines.join('\n'), data: d }
    }
    const st = readPaper(CONFIG)
    if (st === null) return { ok: false, text: '还没有 paper 会话。先 qcta_paper action="init"。' }
    const m = st.metrics || {}
    const lines = [
      'paper 最新交易日：' + st.last_date + '（数据来源 ' + st.data_source + '）',
      '权益 ' + fmtMoney(m.final_equity) + ' / 总收益 ' + fmtPct(m.total_return_pct) + ' / 回撤 ' + fmtPct(m.max_drawdown_pct),
      '持仓 ' + (st.positions || []).length + ' 个：',
    ]
    for (const p of st.positions || []) {
      lines.push('  ' + String(p.symbol).padEnd(4) + (p.direction > 0 ? ' 多 ' : ' 空 ') + p.lots + ' 手 @ ' + p.avg_price + '（现 ' + p.last_price + '）')
    }
    const tail = (st.journal || []).slice(-1)[0]
    if (tail) {
      lines.push('', '最近一日 ' + tail.date + ' 成交 ' + (tail.fills || []).length + ' 笔，拒单 ' + (tail.rejections || []).length + ' 笔')
    }
    return { ok: true, text: lines.join('\n'), data: st }
  },
}

function fmtPct(v) {
  if (v === null || v === undefined || Number.isNaN(v)) return '—'
  return Number(v).toFixed(2) + '%'
}

function fmtNum(v, nd) {
  if (v === null || v === undefined || Number.isNaN(v)) return '—'
  return Number(v).toFixed(nd === undefined ? 2 : nd)
}

function fmtMoney(v) {
  if (v === null || v === undefined || Number.isNaN(v)) return '—'
  return Math.round(Number(v)).toLocaleString('en-US')
}

/* ─────────────────────── 插件本体 ─────────────────────── */

export function apply(ctx, config) {
  CONFIG = config !== null && config !== undefined && typeof config === 'object' ? config : null

  /* ---------- 工具 ---------- */
  ctx.inject(['tools'], (toolsCtx) => {
    const tools = toolsCtx.get('tools')
    if (tools === undefined || tools === null || typeof tools.register !== 'function') return
    const defs = [TOOL_DOCTOR, TOOL_DATA, TOOL_BACKTEST, TOOL_REPORT, TOOL_PAPER]
    for (const d of defs) {
      ctx.effect(() => tools.register(d), 'dsh-quant-cta: ' + d.name)
    }
  })

  /* ---------- Web 路由：面板与只读 API ---------- */
  ctx.inject(['webServer'], (hostCtx) => {
    const server = hostCtx.webServer
    if (server === undefined || server === null || typeof server.register !== 'function') return

    const sendJson = (response, body) => {
      try {
        response.writeHead(200, { 'content-type': 'application/json; charset=utf-8', 'cache-control': 'no-store' })
        response.end(JSON.stringify(body))
      } catch { /* 路由失败不影响工具 */ }
    }

    hostCtx.effect(() => server.register({
      kind: 'exact',
      path: '/dsh-quant-cta/api/overview',
      handler: (_req, response) => {
        const runs = listRuns(CONFIG, 40)
        const latest = runs.length > 0 ? readRun(CONFIG, runs[0].run_id) : null
        const paper = readPaper(CONFIG)
        sendJson(response, {
          generatedAt: new Date().toISOString(),
          engineRoot: engineRootOf(CONFIG),
          python: pickPython(CONFIG),
          runs: runs,
          latest: latest === null ? null : {
            meta: latest.meta || {},
            metrics: latest.metrics || {},
            sleeves: latest.sleeves || {},
            symbols: latest.symbols || {},
            config: latest.config || {},
            curve: latest.curve || [],
            trades: (latest.trades || []).slice(-30),
            rejections: latest.rejections || [],
          },
          paper: paper === null ? null : {
            last_date: paper.last_date,
            data_source: paper.data_source,
            metrics: paper.metrics || {},
            positions: paper.positions || [],
            journal: (paper.journal || []).slice(-40),
          },
        })
      },
    }), 'dsh-quant-cta: overview route')

    hostCtx.effect(() => server.register({
      kind: 'exact',
      path: '/dsh-quant-cta/api/report',
      handler: (request, response) => {
        let runId = ''
        try {
          const u = new URL(request.url, 'http://localhost')
          runId = u.searchParams.get('run') || ''
        } catch { /* ignore */ }
        const d = readRun(CONFIG, runId)
        if (d === null) { sendJson(response, { ok: false, error: '没有找到报告' }); return }
        sendJson(response, d)
      },
    }), 'dsh-quant-cta: report route')

    hostCtx.effect(() => server.register({
      kind: 'exact',
      path: '/dsh-quant-cta/panel',
      handler: (_req, response) => {
        const p = join(PKG_ROOT, 'panel.html')
        let html = '<!doctype html><meta charset="utf-8"><title>量化 CTA 工作台</title><p>panel.html 缺失</p>'
        try { html = readFileSync(p, 'utf8') } catch { /* 用占位 */ }
        try {
          response.writeHead(200, { 'content-type': 'text/html; charset=utf-8', 'cache-control': 'no-store' })
          response.end(html)
        } catch { /* ignore */ }
      },
    }), 'dsh-quant-cta: panel route')
  })

  /* ---------- 系统提示片段 ---------- */
  ctx.inject(['systemPrompt'], (promptCtx) => {
    const prompt = promptCtx.get('systemPrompt')
    if (prompt === undefined || prompt === null || typeof prompt.section !== 'function') return
    const text = [
      '## 量化 CTA 工作台（qcta_*）',
      '当用户要跑期货回测、看策略绩效、做模拟盘时用这些工具，不要凭空推算行情或收益：',
      '- qcta_doctor：先自检（有没有 tqsdk / 快期凭据 / 行情缓存）。没有凭据时引擎只能跑合成数据，必须向用户说明。',
      '- qcta_backtest 跑完整回测；qcta_report 读报告（action="metrics" 省 token）。',
      '- qcta_paper 做模拟盘：init 建会话、step 推进到最新交易日。',
      '口径纪律：回测收益是历史统计，不是承诺；报告里 data_source=synthetic 时绝不可当作真实行情结论。',
      '本插件不发真实委托，不要向用户暗示可以自动实盘下单。',
    ].join('\n')
    promptCtx.effect(
      () => prompt.section({ name: 'quant-cta', order: 156, text }),
      'dsh-quant-cta: prompt section',
    )
  })
}
