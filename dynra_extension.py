import inspect
import sys
from pathlib import Path

from dynra_core import NamespaceManager


def load_ipython_extension(ipython):
    manager = NamespaceManager(shell=ipython)
    # --- AUTO-NAMESPACE SYNC ---
    def auto_sync_namespace():
        """Switch to the discovered project module for evaluated file code."""
        paths = {
            info.path: name for name, info in manager.project.modules.items()
        }
        for frame in inspect.stack():
            if frame.filename.startswith("<"):
                continue
            try:
                module_name = paths.get(Path(frame.filename).resolve())
            except OSError:
                module_name = None
            if module_name:
                if manager.current_name != module_name:
                    manager.in_md(module_name)
                break

    # Enable Autoreload
    if not ipython.extension_manager.was_loaded('autoreload'):
        ipython.extension_manager.load_extension('autoreload')
    ipython.run_line_magic('autoreload', '2')
    
    # Enable Live Patching & Auto-Sync
    ipython.events.register('pre_execute', auto_sync_namespace) # Sync before eval
    ipython.events.register('pre_execute', manager.pre_execute)
    ipython.events.register('post_execute', manager.post_execute)
    
    # Initialize 'user' namespace
    manager._init_ipython_ns(sys.modules['user'].__dict__)
    ipython.user_ns = sys.modules['user'].__dict__
    ipython.user_module = sys.modules['user']
    
    print("Dynra extension loaded with project-aware namespace sync.")
    print("Commands: create_md(name), require_md(name), in_md(name), ls_md(), help_dynra()")

def unload_ipython_extension(ipython):
    # Optional: cleanup hooks if needed
    pass
