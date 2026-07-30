import builtins
import sys
import os
from dynra_core import NamespaceManager

def load_ipython_extension(ipython):
    manager = NamespaceManager(shell=ipython)
    
    # Inject globals
    builtins.md = manager.md
    builtins.in_md = manager.in_md
    builtins.ls_md = manager.ls_md
    builtins.help_dynra = manager.help_dynra
    
    # --- AUTO-NAMESPACE SYNC ---
    def auto_sync_namespace():
        """Automatically switch to the module namespace of the file being edited."""
        # Try to find the filename in the execution metadata
        # VS Code sends code with a specific filename in the history/stack
        import inspect
        for frame in inspect.stack():
            if frame.filename.endswith('.py') and not frame.filename.startswith('<ipython-input'):
                # Convert absolute path to module name
                rel_path = os.path.relpath(frame.filename, os.getcwd())
                if rel_path.endswith('.py'):
                    mod_name = rel_path[:-3].replace(os.sep, '.')
                    if mod_name.endswith('.__init__'):
                        mod_name = mod_name[:-9]
                    
                    if manager.current_name != mod_name:
                        manager.in_md(mod_name)
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
    
    print("Dynra extension loaded with Auto-Namespace Sync.")
    print("Commands: md(name), in_md(name), ls_md(), help_dynra()")

def unload_ipython_extension(ipython):
    # Optional: cleanup hooks if needed
    pass
