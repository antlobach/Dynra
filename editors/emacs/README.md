# Dynra for Emacs

`dynra.el` provides project-aware, CIDER-style live Python development:

- runtime discovery by project root, without guessing ports;
- `dynra-jack-in` to start the project's `uv run dynra --port 0` process;
- inline result and error overlays plus echo-area output;
- evaluation of statements, functions/classes, regions, and buffers;
- automatic connection when a Python file opens.

## Install

Add the plugin directory to Emacs' load path:

```elisp
(add-to-list 'load-path "/path/to/Dynra/editors/emacs")
(require 'dynra)
(dynra-auto-mode 1)
```

A running runtime is connected automatically when a Python file under the same
project root opens. To start it manually, visit a project Python file and run
`M-x dynra-jack-in` or press `C-c C-x j`. That key is available in Python
buffers before the first connection.

`dynra-auto-mode` discovers existing runtimes when Python files open. Automatic
jack-in is separate and opt-in; set `(setq dynra-auto-jack-in t)` if desired.

If Emacs cannot find `uv`, Dynra checks the Emacs `PATH` and common install
locations (`~/.local/bin`, Homebrew paths, and Linuxbrew). For another location,
set `dynra-command` to use its absolute path, for example:

```elisp
(setq dynra-command '("/absolute/path/to/uv" "run" "dynra" "--port" "0"))
```

Dynra must be a development dependency of the Python project:

```bash
uv add --dev dynra
```

## Commands

| Key | Command | Action |
|---|---|---|
| `C-c C-x j` | `dynra-jack-in` | Start Dynra for this project and connect |
| `C-c C-x c` | `dynra-connect` | Connect to an existing project runtime |
| `C-x C-e`, `C-c C-e` | `dynra-eval-last-sexp` | Evaluate the Python statement at point |
| `C-c C-c`, `C-M-x` | `dynra-eval-defun-at-point` | Evaluate the function or class at point |
| `C-c C-r` | `dynra-eval-region` | Evaluate the selected region |
| `C-c C-k` | `dynra-load-buffer` | Evaluate the complete buffer |
| `C-c C-z` | `dynra-switch-to-repl` | Open the Emacs-managed Dynra REPL |
| `C-c C-x b` | `dynra-bootstrap` | Run the configured project bootstrap |
| `C-c C-x s` | `dynra-status` | Show runtime, module, and generation |
| `C-c C-x q` | `dynra-quit` | Stop the runtime |

Results follow CIDER's interaction model: inline at the evaluated line and in
the echo area. Customize `dynra-result-display`,
`dynra-result-overlay-position`, and `dynra-result-overlay-duration`.

When Emacs has the Python tree-sitter grammar, `C-x C-e` selects the complete
top-level syntax form containing point. Expressions, loops, decorated
definitions, functions, and classes are therefore evaluated without truncating
their bodies. The plugin falls back to `python-nav` when the grammar is absent.

## Start evaluating

Dynra is the Python runtime, analogous to the nREPL process that CIDER starts.
Install Dynra in the project, open a Python file, and jack in:

```bash
uv init
uv add --dev dynra
emacs app.py
```

Press `C-c C-x j`. Emacs starts `uv run dynra --port 0` in that project,
discovers the registered runtime, and connects the buffer. Then evaluate:

```python
40 + 2
```

Put point on that statement and press `C-x C-e`; `=> 42` appears inline.
`C-c C-c` reevaluates the function or class at point while preserving the
running Python process and compatible live references.

The connection belongs to the project, not to an individual buffer. Switching
between project files keeps one runtime while each evaluation derives its
module from the current file (`module.py` → `module`, `module2.py` →
`module2`).

## Emacs MCP server

The Emacs MCP server is separate from Dynra. It lets an MCP-capable coding
client inspect Emacs; Dynra connects Emacs to the live Python runtime. A working
server configuration is:

```elisp
(add-to-list 'load-path "/path/to/emacs-mcp-server")
(require 'mcp-server)
(if after-init-time
    (mcp-server-start-unix)
  (add-hook 'emacs-startup-hook #'mcp-server-start-unix))
```

The default socket is `~/.emacs.d/emacs-mcp-server.sock` unless
`mcp-server-socket-directory` is customized.
