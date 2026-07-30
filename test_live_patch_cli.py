import pexpect
import subprocess
import sys
import time
import os

def test_live_patching_via_cli():
    print("Starting Dynra server...")
    python_exec = sys.executable
    server = pexpect.spawn(f"{python_exec} dynra.py", encoding='utf-8')
    
    try:
        server.expect(r"user> ", timeout=10)
        print("Server ready.")
        
        def run_client(code, label):
            print(f"\n[Step: {label}]")
            print(f"Executing: {code}")
            result = subprocess.run([python_exec, "dynra_client.py", code], 
                                  capture_output=True, text=True)
            output = result.stdout.strip()
            print(f"Output: {output}")
            return output

        # 1. Setup mod_a with a function
        run_client('md("mod_a")', "Create mod_a")
        run_client('in_md("mod_a")', "Enter mod_a")
        run_client('def foo(): return 1', "Define foo() -> 1")
        
        # 2. Setup mod_b and IMPORT foo from mod_a
        run_client('md("mod_b")', "Create mod_b")
        run_client('in_md("mod_b")', "Enter mod_b")
        run_client('from mod_a import foo', "Import foo FROM mod_a")
        output_1 = run_client('foo()', "Check foo() in mod_b")
        
        # 3. Go back to mod_a and REDEFINE foo
        run_client('in_md("mod_a")', "Return to mod_a")
        run_client('def foo(): return 100', "Redefine foo() -> 100")
        
        # 4. Verify mod_b's imported foo is NOW UPDATED
        run_client('in_md("mod_b")', "Return to mod_b")
        output_2 = run_client('foo()', "Check foo() in mod_b AGAIN")
        
        print("\n--- RESULTS ---")
        print(f"Initial foo() in mod_b: {output_1}")
        print(f"Patched foo() in mod_b: {output_2}")
        
        if "100" in output_2:
            print("\nSUCCESS: Live REPL Patching verified! mod_b's imported reference was updated in-place.")
        else:
            print(f"\nFAILURE: mod_b is still using the old function object: {output_2}")
            
    finally:
        server.terminate(force=True)

if __name__ == "__main__":
    test_live_patching_via_cli()
