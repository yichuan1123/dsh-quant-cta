/**
 * 客户端半身冒烟：在 vm 沙箱里跑 client/client.js，验证它注册了「开始」入口与右侧栏 tab。
 *
 * 不依赖浏览器，也不需要真的 react（用 stub）。CI 里能跑：node tools/smoke-client.mjs
 */

import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import vm from 'node:vm'

const HERE = dirname(fileURLToPath(import.meta.url))
const ROOT = join(HERE, '..')
const SOURCE = readFileSync(join(ROOT, 'client', 'client.js'), 'utf8')

let failures = 0
function assert(cond, msg) {
  if (!cond) {
    console.error('FAIL: ' + msg)
    failures += 1
    return
  }
  console.log('ok   ' + msg)
}

/* ── 1. 在沙箱里执行客户端 bundle ── */
let loaded = null
const sandbox = {
  window: { __ModuleLoader__: { load: (m) => { loaded = m } } },
  console,
}
const requireStub = (name) => {
  if (name === 'react') {
    return { createElement: (type, props) => ({ type, props }) }
  }
  throw new Error('客户端不该 require 这个：' + name)
}
vm.createContext(sandbox)
vm.runInContext(SOURCE, sandbox, { filename: 'client/client.js' })

assert(loaded !== null, '__ModuleLoader__.load 被调用')
assert(loaded.id === 'dsh-quant-cta', '模块 id 正确')
const mod = loaded.factory(requireStub)
assert(typeof mod.apply === 'function', '导出 apply')
assert(Array.isArray(mod.inject) && mod.inject.includes('slots') && mod.inject.includes('sidebarRightTabs'),
  'inject 声明了 slots 与 sidebarRightTabs')

/* ── 2. 用假 ctx 跑 apply，检查注册面 ── */
const calls = []
const ctx = {
  effect(fn) { return fn() },
  sidebarRightTabs: { register(def) { calls.push({ type: 'tab', def }); return () => {} } },
  slots: {
    inject(name, fn) { calls.push({ type: 'inject', name }); return fn() },
    register(def, comp) { calls.push({ type: 'slot', def, comp }); return () => {} },
  },
  sidebarRight: { openTab(kind) { calls.push({ type: 'open', kind }) } },
}
mod.apply(ctx)

const tab = calls.find((c) => c.type === 'tab')
assert(tab !== undefined, '注册了右侧栏 tab 类型')
assert(tab.def.kind === 'quantcta', 'tab kind = quantcta')
assert(typeof tab.def.title === 'function' && tab.def.title().includes('量化 CTA'), 'tab 标题可读')

const guide = tab.def.guide
assert(Array.isArray(guide) && guide.length === 1, 'guide 提供 1 个「开始」入口')
assert(guide[0].title().includes('量化 CTA'), '入口标题：' + guide[0].title())
assert(typeof guide[0].order === 'number', '入口排序值是数字')
assert(typeof guide[0].description() === 'string' && guide[0].description().length > 5, '入口有描述')

const inject = calls.find((c) => c.type === 'inject')
assert(inject !== undefined && inject.name === 'sidebar.right.pane.tab', '注入 sidebar.right.pane.tab')

const slot = calls.find((c) => c.type === 'slot')
assert(slot !== undefined, '注册了 tab 内容')
assert(slot.def.name === 'sidebar.right.pane.tab' && slot.def.key === 'dsh-quant-cta', 'slot key 正确')
assert(typeof slot.comp === 'function', '内容是一个组件函数')

/* ── 3. 组件渲染成一个指向面板的 iframe ── */
const node = slot.comp({})
assert(node.type === 'iframe', '组件渲染 iframe')
assert(node.props.src === '/dsh-quant-cta/panel', 'iframe 指向面板路由')
assert(String(node.props.title).length > 0, 'iframe 有 title（无障碍）')
// 回归：iframe 不能带 sandbox —— 少了 allow-modals 会把面板里的 confirm() 拦掉
assert(node.props.sandbox === undefined, 'iframe 不带 sandbox（否则 confirm 会被拦）')

/* ── 4. 暴露的调试入口能打开 tab ── */
assert(sandbox.window.__dshQuantCta !== undefined, '挂上了 window.__dshQuantCta')
sandbox.window.__dshQuantCta.open()
assert(calls.some((c) => c.type === 'open' && c.kind === 'quantcta'), 'open() 调用了 sidebarRight.openTab')

if (failures > 0) {
  console.error('\n' + failures + ' 项失败')
  process.exit(1)
}
console.log('\n客户端冒烟通过。')
