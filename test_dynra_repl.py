import sys
import time
from pathlib import Path

import pexpect


PROJECT_ROOT = Path(__file__).parent


def test_repl_autoreload_and_namespaces(tmp_path):
    package = tmp_path / "pkg"
    package.mkdir()
    (package / "__init__.py").write_text("")
    (package / "mod_a.py").write_text("def get_val(): return 1\n")
    (package / "mod_b.py").write_text(
        "from pkg.mod_a import get_val\ndef check(): return get_val()\n"
    )

    child = pexpect.spawn(
        sys.executable,
        [str(PROJECT_ROOT / "dynra.py")],
        cwd=str(tmp_path),
        encoding="utf-8",
        timeout=10,
    )

    try:
        child.expect(r"user> ")

        child.sendline('in_md("pkg.mod_a")')
        child.expect(r"pkg.mod_a> ")
        child.sendline("get_val()")
        child.expect(r"Out\[\d+\]:")
        child.expect_exact("1")
        child.expect(r"pkg.mod_a> ")

        child.sendline('in_md("pkg.mod_b")')
        child.expect(r"pkg.mod_b> ")
        child.sendline("check()")
        child.expect(r"Out\[\d+\]:")
        child.expect_exact("1")
        child.expect(r"pkg.mod_b> ")

        time.sleep(1.1)
        (package / "mod_a.py").write_text("def get_val(): return 100\n")
        child.sendline("check()")
        child.expect(r"Out\[\d+\]:")
        child.expect_exact("100")
        child.expect(r"pkg.mod_b> ")

        child.sendline('md("temp")')
        child.expect(r"module 'temp'")
        child.expect(r"pkg.mod_b> ")
        child.sendline('in_md("temp")')
        child.expect(r"temp> ")
        child.sendline('def foo(): return "bar"')
        child.expect(r"temp> ")
        child.sendline("foo()")
        child.expect(r"Out\[\d+\]:")
        child.expect_exact("'bar'")
        child.expect(r"temp> ")

        child.sendline('in_md("user")')
        child.expect(r"user> ")
        child.sendline("import temp; temp.foo()")
        child.expect(r"Out\[\d+\]:")
        child.expect_exact("'bar'")
        child.expect(r"user> ")
    finally:
        child.terminate(force=True)
