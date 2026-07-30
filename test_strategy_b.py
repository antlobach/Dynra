import sys
import os
import time
import importlib
from IPython.terminal.ipapp import TerminalIPythonApp

def test_strategy_b_live():
    # Setup test project files
    root = os.getcwd() + "/test_project"
    os.makedirs(root + "/pkg", exist_ok=True)
    with open(root + "/pkg/__init__.py", "w") as f: f.write("")
    with open(root + "/pkg/mod_a.py", "w") as f: f.write("def get_val(): return 1\n")
    with open(root + "/pkg/mod_b.py", "w") as f: f.write("from pkg.mod_a import get_val\ndef check(): return get_val()\n")

    sys.path.insert(0, root)
    
    # Initialize the IPython autoreload components.
    app = TerminalIPythonApp.instance()
    app.initialize(argv=[])
    shell = app.shell
    shell.extension_manager.load_extension('autoreload')
    shell.run_line_magic('autoreload', '2')
    
    print("--- 1. Initial State ---")
    # CRITICAL: Import AFTER enabling autoreload
    import pkg.mod_a
    import pkg.mod_b
    shell.user_ns['pkg'] = pkg
    
    print(f"pkg.mod_a.get_val() -> {pkg.mod_a.get_val()}")
    print(f"pkg.mod_b.check() -> {pkg.mod_b.check()}")

    print("\n--- 2. Modifying pkg/mod_a.py on disk ---")
    time.sleep(1.1) 
    with open(root + "/pkg/mod_a.py", "w") as f: f.write("def get_val(): return 100\n")
    
    # Simulate IPython pre-prompt check
    reloader = shell.magics_manager.registry['AutoreloadMagics']._reloader
    reloader.check()
    
    print(f"pkg.mod_a.get_val() -> {pkg.mod_a.get_val()}")
    print(f"pkg.mod_b.check() -> {pkg.mod_b.check()}")

    if pkg.mod_b.check() == 100:
        print("\nSUCCESS: Strategy B worked! Cross-module patching verified.")
    else:
        print("\nFAILURE: Still using old object.")

if __name__ == "__main__":
    test_strategy_b_live()
