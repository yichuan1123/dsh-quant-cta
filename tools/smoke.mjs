/**
 * 插件冒烟测试（不依赖 DSH 宿主，CI 里也能跑）：node tools/smoke.mjs
 *
 * 做三件事：
 *   1) 用假的 ctx 装配 lib/index.js，检查注册了 5 个工具 + 4 条路由 + 1 段系统提示
 *   2) 把注册的路由挂到真的 http.createServer 上，逐个打一遍（含凭据保存/清除）
 *   3) 断言「密码绝不出现在任何响应体里」
 *
 * 凭据相关测试全部指向 QCTA_CRED_PATH 指定的临时文件，绝不碰真实账号。
 */

import http from 'node:http'
import { existsSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

import { apply, name } from '../lib/index.js'

const CRED_PATH = join(tmpdir(), 'qcta-smoke-credentials.json')
process.env.QCTA_CRED_PATH = CRED_PATH
if (existsSync(CRED_PATH)) rmSync(CRED_PATH)

const tools = new Map()
const routes = new Map()
const promptSections = []

function fakeCtx() {
  return {
    effect(fn) { return fn() },
    inject(deps, cb) {
      if (deps.includes('tools')) {
        cb({
          get(key) {
            if (key !== 'tools') return undefined
            return { register(def) { tools.set(def.name, def); return () => tools.delete(def.name) } }
          },
        })
      }
      if (deps.includes('webServer')) {
        cb({
          effect(fn) { return fn() },
          webServer: { register(route) { routes.set(route.path, route); return () => routes.delete(route.path) } },
        })
      }
      if (deps.includes('systemPrompt')) {
        cb({
          effect(fn) { return fn() },
          get() { return { section(s) { promptSections.push(s.name); return () => {} } } },
        })
      }
    },
  }
}

let failures = 0
function assert(cond, msg) {
  if (!cond) {
    console.error('FAIL: ' + msg)
    failures += 1
    return
  }
  console.log('ok   ' + msg)
}

apply(fakeCtx(), { pythonPath: process.env.QCTA_PYTHON || undefined })

/* ── 1. 注册面 ── */
assert(name === 'dsh-quant-cta', '插件名正确')
assert(tools.size === 5, '注册 5 个工具，实际 ' + tools.size)
for (const t of ['qcta_doctor', 'qcta_data', 'qcta_backtest', 'qcta_report', 'qcta_paper']) {
  assert(tools.has(t), '工具存在：' + t)
}
for (const [n, def] of tools) {
  assert(typeof def.description === 'string' && def.description.length > 20, n + ' 有描述')
  assert(def.parameters && def.parameters.type === 'object', n + ' 参数 schema 合法')
  assert(typeof def.execute === 'function', n + ' 有 execute')
}
assert(routes.has('/dsh-quant-cta/api/overview'), '路由 overview 已注册')
assert(routes.has('/dsh-quant-cta/api/report'), '路由 report 已注册')
assert(routes.has('/dsh-quant-cta/api/credentials'), '路由 credentials 已注册')
assert(routes.has('/dsh-quant-cta/api/credentials/test'), '路由 credentials/test 已注册')
assert(routes.has('/dsh-quant-cta/panel'), '路由 panel 已注册')
assert(promptSections.includes('quant-cta'), '注册了系统提示片段')

/* ── 2. 起真 HTTP 服务，把路由挂上 ── */
const server = http.createServer((req, res) => {
  const url = new URL(req.url, 'http://localhost')
  const route = routes.get(url.pathname)
  if (!route) { res.writeHead(404); res.end('not found'); return }
  Promise.resolve(route.handler(req, res)).catch((e) => {
    try { res.writeHead(500); res.end(String(e)) } catch { /* ignore */ }
  })
})
await new Promise((r) => server.listen(0, '127.0.0.1', r))
const base = 'http://127.0.0.1:' + server.address().port

async function getJson(path, opts) {
  const res = await fetch(base + path, opts)
  const text = await res.text()
  let json = null
  try { json = JSON.parse(text) } catch { /* 非 JSON */ }
  return { status: res.status, text, json }
}

try {
  const ov = await getJson('/dsh-quant-cta/api/overview')
  assert(ov.status === 200 && ov.json !== null && Array.isArray(ov.json.runs), 'GET overview 返回 runs 列表')

  const panel = await getJson('/dsh-quant-cta/panel')
  assert(panel.status === 200 && panel.text.includes('数据源账号'), 'GET panel 返回含「数据源账号」的页面')

  const emptyCred = await getJson('/dsh-quant-cta/api/credentials')
  assert(emptyCred.json !== null && emptyCred.json.configured === false, '初始凭据状态为未配置')

  const DUMMY_USER = '13800001234'
  const DUMMY_PASS = 'smoke-only-not-a-real-password'
  const saved = await getJson('/dsh-quant-cta/api/credentials', {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ user: DUMMY_USER, password: DUMMY_PASS }),
  })
  assert(saved.json !== null && saved.json.ok === true, 'POST credentials 保存成功')
  assert(saved.json.status && saved.json.status.masked_user.indexOf('***') >= 0, '返回的是脱敏账号')
  assert(!saved.text.includes(DUMMY_PASS), '响应体里没有明文密码')
  assert(!saved.text.includes(DUMMY_USER), '响应体里没有完整账号')

  const after = await getJson('/dsh-quant-cta/api/credentials')
  assert(after.json.configured === true && after.json.source === 'file', '保存后状态为已配置(file)')
  assert(!after.text.includes(DUMMY_PASS), '状态查询也不回显密码')

  const cleared = await getJson('/dsh-quant-cta/api/credentials', { method: 'DELETE' })
  assert(cleared.json !== null && cleared.json.configured === false, 'DELETE 后回到未配置')
} finally {
  server.close()
  if (existsSync(CRED_PATH)) rmSync(CRED_PATH)
}

/* ── 3. 工具端到端（真引擎） ── */
const doctor = await tools.get('qcta_doctor').execute({})
assert(doctor.ok === true && doctor.text.includes('python'), 'qcta_doctor 执行成功')

const bt = await tools.get('qcta_backtest').execute({ pool: 'starter', start: '2021-01-01', name: 'smoke_test' })
assert(bt.ok === true && bt.run_id === 'smoke_test', 'qcta_backtest 执行成功')
assert(bt.metrics && typeof bt.metrics.cagr_pct === 'number', '回测返回结构化指标')
console.log('     smoke 回测：CAGR ' + bt.metrics.cagr_pct + '% / 夏普 ' + bt.metrics.sharpe + ' / 数据源 ' + bt.data_source)

const rep = await tools.get('qcta_report').execute({ action: 'get', run_id: 'smoke_test' })
assert(rep.ok === true && rep.text.includes('核心指标'), 'qcta_report get 返回报告正文')

const list = await tools.get('qcta_report').execute({ action: 'list' })
assert(list.ok === true && list.data.length > 0, 'qcta_report list 能读到回测记录')

const paper = await tools.get('qcta_paper').execute({ action: 'status' })
assert(typeof paper.ok === 'boolean', 'qcta_paper status 可调用')

if (failures > 0) {
  console.error('\n' + failures + ' 项失败')
  process.exit(1)
}
console.log('\n全部冒烟检查通过。')
