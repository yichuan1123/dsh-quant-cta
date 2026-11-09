"""文档与实现的数量一致性检查。

文档里写死的数字（品种数、单测数、工具数、路由数）最容易在改动后过期。
这个脚本把「文档说的」和「代码实际的」逐条对上，任何一条不符就失败。

用法：python tools/check_consistency.py
"""

import pathlib
import re
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent


def main():
    specs_src = (ROOT / "python/qcta/specs.py").read_text(encoding="utf-8")
    n_specs = len(re.findall(r'^    "[A-Za-z]+": ContractSpec\(', specs_src, re.M))

    lib = (ROOT / "lib" / "index.js").read_text(encoding="utf-8")
    n_tools = lib.count("  name: 'qcta_")
    n_routes = lib.count("path: '/dsh-quant-cta")

    readme = (ROOT / "README.md").read_text(encoding="utf-8")

    proc = subprocess.run(
        [sys.executable, "-m", "unittest", "discover", "-s", "python/tests", "-t", "python"],
        cwd=str(ROOT), capture_output=True, text=True,
    )
    m = re.search(r"Ran (\d+) tests", proc.stderr + proc.stdout)
    n_tests = int(m.group(1)) if m else -1

    tools_all = all(("qcta_" + n) in readme for n in ("doctor", "data", "backtest", "report", "paper"))
    routes_all = "/dsh-quant-cta/panel" in readme

    checks = [
        ("品种规格表条数", n_specs, 28, "28 个品种" in readme),
        ("插件工具数", n_tools, 5, tools_all),
        ("Web 路由数", n_routes, 3, routes_all),
        ("单元测试数", n_tests, 35, "35 个单测" in readme),
    ]

    ok = True
    for name, actual, expected, documented in checks:
        good = actual == expected
        ok = ok and good
        print("%s %-12s 实际=%s 期望=%s 文档已同步=%s" % (
            "OK  " if good else "FAIL", name, actual, expected, "是" if documented else "否"))
        if not documented:
            ok = False

    print("\n" + ("文档与实现一致" if ok else "存在不一致，请修正"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
