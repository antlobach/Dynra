import os
import sys
import json
import subprocess

def register_dynra_kernel():
    # 1. Ensure ipykernel is installed
    subprocess.run([sys.executable, "-m", "pip", "install", "ipykernel"], check=True)
    
    # 2. Get kernelspec directory
    kernel_name = "dynra"
    display_name = "Dynra (Python with Clojure Magic)"
    
    # Path where Jupyter stores kernelspecs
    user_kernel_path = os.path.expanduser(f"~/.local/share/jupyter/kernels/{kernel_name}")
    os.makedirs(user_kernel_path, exist_ok=True)
    
    # 3. Create kernel.json
    kernel_json = {
        "argv": [
            sys.executable,
            "-m", "ipykernel_launcher",
            "-f", "{connection_file}",
            "--InteractiveShellApp.extensions=['dynra_extension']"
        ],
        "display_name": display_name,
        "language": "python",
        "env": {
            "PYTHONPATH": os.getcwd()
        }
    }
    
    json_path = os.path.join(user_kernel_path, "kernel.json")
    with open(json_path, "w") as f:
        json.dump(kernel_json, f, indent=2)
    
    print(f"Kernel '{display_name}' registered successfully at: {json_path}")
    print("\nYou can now select this kernel in VS Code or Jupyter!")

if __name__ == "__main__":
    register_dynra_kernel()
