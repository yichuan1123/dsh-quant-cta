import { spawn } from 'node:child_process'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

process.env.QCTA_CRED_PATH = join(tmpdir(), 'qcta-diag-cred2.json')
const py = process.env.QCTA_PYTHON
const child = spawn(py, ['-m', 'qcta', 'auth', 'login', '--stdin', '--json'], {
  cwd: join(process.cwd(), 'python'),
  env: { ...process.env, PYTHONIOENCODING: 'utf-8' },
})
let out = '', err = ''
child.stdout.on('data', (c) => { out += String(c) })
child.stderr.on('data', (c) => { err += String(c) })
child.stdin.write(JSON.stringify({ user: '13000000000', password: 'dummy-pass' }))
child.stdin.end()
child.on('close', (code) => {
  console.log('exit', code)
  console.log('--- stdout ---'); console.log(out.slice(0, 800))
  console.log('--- stderr ---'); console.log(err.slice(-1500))
})
