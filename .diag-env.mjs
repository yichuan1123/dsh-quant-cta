import { spawn } from 'node:child_process'
const py = process.env.QCTA_PYTHON
const code = [
  "import os, tempfile, getpass",
  "print('TEMP', os.environ.get('TEMP'))",
  "print('TMP', os.environ.get('TMP'))",
  "print('TMPDIR', os.environ.get('TMPDIR'))",
  "print('tempdir', tempfile.gettempdir())",
  "print('cwd', os.getcwd())",
  "print('user', getpass.getuser())",
  "p = os.path.join(tempfile.gettempdir(), 'qcta-probe.tmp')",
  "print('exists', os.path.exists(p))",
  "try:",
  "    open(p, 'w').write('x'); print('write ok'); os.remove(p)",
  "except Exception as e:",
  "    print('write FAILED', type(e).__name__, e)",
].join("\n")
const child = spawn(py, ['-c', code], { cwd: process.cwd(), env: { ...process.env, PYTHONIOENCODING: 'utf-8' } })
let out = '', err = ''
child.stdout.on('data', (c) => { out += String(c) })
child.stderr.on('data', (c) => { err += String(c) })
child.on('close', (code2) => { console.log('exit', code2); console.log(out); if (err) console.log('ERR', err.slice(-400)) })
