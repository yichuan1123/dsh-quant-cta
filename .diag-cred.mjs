import http from 'node:http'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { apply } from './lib/index.js'

process.env.QCTA_CRED_PATH = join(tmpdir(), 'qcta-diag-cred.json')
const routes = new Map()
const ctx = {
  effect(fn) { return fn() },
  inject(deps, cb) {
    if (deps.includes('tools')) cb({ get: () => ({ register() { return () => {} } }) })
    if (deps.includes('webServer')) cb({ effect(fn) { return fn() }, webServer: { register(r) { routes.set(r.path, r); return () => {} } } })
    if (deps.includes('systemPrompt')) cb({ effect(fn) { return fn() }, get: () => ({ section() { return () => {} } }) })
  },
}
apply(ctx, { pythonPath: process.env.QCTA_PYTHON })
const server = http.createServer((req, res) => {
  const u = new URL(req.url, 'http://localhost')
  const r = routes.get(u.pathname)
  if (!r) { res.writeHead(404); res.end('nf'); return }
  Promise.resolve(r.handler(req, res)).catch((e) => { res.writeHead(500); res.end(String(e)) })
})
await new Promise((r) => server.listen(0, '127.0.0.1', r))
const base = 'http://127.0.0.1:' + server.address().port
const res = await fetch(base + '/dsh-quant-cta/api/credentials', {
  method: 'POST', headers: { 'content-type': 'application/json' },
  body: JSON.stringify({ user: '13000000000', password: 'dummy-pass' }),
})
const text = await res.text()
console.log('status', res.status)
console.log('raw:', text.slice(0, 500))
server.close()
