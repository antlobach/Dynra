import subprocess
import sys
import os
import time
import pexpect

def run_demo_test():
    print("🚀 Starting Dynra Demo Test...")
    python_exec = sys.executable
    server = pexpect.spawn(f"{python_exec} dynra.py", encoding='utf-8')
    
    try:
        server.expect(r"user> ", timeout=10)
        print("✅ Dynra Server Live.")
        
        def cli(code):
            return subprocess.run([python_exec, "dynra_client.py", code], 
                                  capture_output=True, text=True).stdout.strip()

        # 1. Setup Initial State in API Service
        print("\n--- Phase 1: Initial Setup ---")
        cli("in_md('dynra_demo.api.service')")
        cli("u1 = process_new_user('Alice')")
        cli("u2 = process_new_user('Bob')")
        print(f"Initial Report: {cli('get_report()')}")

        # 2. Test Function Hot-Patching (Cross-Module)
        print("\n--- Phase 2: Patching Core Math ---")
        cli("in_md('dynra_demo.core.math')")
        # Extreme change to 'add'
        cli("def add(a, b): return (a + b) * 1000") 
        
        cli("in_md('dynra_demo.api.service')")
        # Create a new user to trigger the patched 'add' inside service
        output = cli("u3 = process_new_user('Charlie')") 
        print(f"Charlie result (should show * 1000): {output}")

        # 3. Test Class Instance Evolution (Common Lisp Style)
        print("\n--- Phase 3: Evolving Core Models ---")
        cli("in_md('dynra_demo.core.models')")
        # Redefine User class with more descriptive info() and update hook
        cli("""
class User:
    def __init__(self, username, status="online"):
        self.username = username
        self.status = status
    def info(self):
        return f"[V2] {self.username} is currently {self.status.upper()}!"
    def __dynra_update__(self):
        self.status = 'patched'
""")
        
        # 4. Check the "Living" Instances in Service
        print("\n--- Phase 4: Checking Living Instances ---")
        cli("in_md('dynra_demo.api.service')")
        report = cli("get_report()")
        print(f"Updated Report: {report}")
        
        if "[V2]" in report and "PATCHED" in report:
            print("\n🎉 ALL TESTS PASSED! Your Python app is now a living organism.")
        else:
            print("\n❌ Test Failed. Some state did not propagate.")

    finally:
        server.terminate(force=True)

if __name__ == "__main__":
    run_demo_test()
