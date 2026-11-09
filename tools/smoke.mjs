/**
 * 插件冒烟测试：用假的 ctx 把 lib/index.js 装起来，验证
 *   1) apply() 不抛异常、注册 5 个工具 + 3 条路由
 *   2) qcta_doctor / qcta_backtest / qcta_report 端到端能跑（调真正的 Python 引擎）
 *
 * 不依赖 DSH 宿主，CI 里也能跑：node tools/smoke.mjs
 */

import { apply, name } from '../lib/index.js'

const tools = new Map()
const routes = new Map()
const promptSections = []

function fakeCtx() {
  const ctx = {
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
  return ctx
}

function assert(cond, msg) {
  if (!cond) {
    console.error('FAIL: ' + msg)
    process.exitCode = 1
    throw new Error(msg)
  }
  console.log('ok   ' + msg)
}

const ctx = fakeCtx()
apply(ctx, { pythonPath: process.env.QCTA_PYTHON || undefined })

assert(typeof name === 'string' && name === 'dsh-quant-cta', '插件名正确')
assert(tools.size === 5, '注册 5 个工具，实际 ' + tools.size + '：' + [...tools.keys()].join(','))
for (const t of ['qcta_doctor', 'qcta_data', 'qcta_backtest', 'qcta_report', 'qcta_paper']) {
  assert(tools.has(t), '工具存在：' + t)
}
assert(routes.size === 3, '注册 3 条路由，实际 ' + routes.size + '：' + [...routes.keys()].join(','))
assert(promptSections.includes('quant-cta'), '注册了系统提示片段')

for (const [n, def] of tools) {
  assert(typeof def.description === 'string' && def.description.length > 20, n + ' 有描述')
  assert(def.parameters && def.parameters.type === 'object', n + ' 参数 schema 合法')
  assert(typeof def.execute === 'function', n + ' 有 execute')
}

const doctor = await tools.get('qcta_doctor').execute({})
assert(doctor.ok === true, 'qcta_doctor 执行成功')
assert(typeof doctor.text === 'string' && doctor.text.includes('python'), 'doctor 输出包含环境检查')

const bt = await tools.get('qcta_backtest').execute({ pool: 'starter', start: '2021-01-01', name: 'smoke_test' })
assert(bt.ok === true, 'qcta_backtest 执行成功')
assert(typeof bt.run_id === 'string' && bt.run_id === 'smoke_test', '回测返回 run_id')
assert(bt.metrics && typeof bt.metrics.cagr_pct === 'number', '回测返回结构化指标')
console.log('     smoke 回测：CAGR ' + bt.metrics.cagr_pct + '% / 夏普 ' + bt.metrics.sharpe + ' / 数据源 ' + bt.data_source)

const list = await tools.get('qcta_report').execute({ action: 'list' })
assert(list.ok === true && list.data.length > 0, 'qcta_report list 能读到回测记录')

const paper = await tools.get('qcta_paper').execute({ action: 'status' })
assert(typeof paper.ok === 'boolean', 'qcta_paper status 可调用')

console.log('\n全部冒烟检查通过。')
