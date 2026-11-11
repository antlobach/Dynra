<p align="center">
  <img src="https://raw.githubusercontent.com/antlobach/Dynra/main/assets/dynra-logo.svg" alt="Dynra" width="860">
</p>

<p align="center">
  <a href="https://github.com/antlobach/Dynra/actions/workflows/ci.yml"><img src="https://img.shields.io/github/actions/workflow/status/antlobach/Dynra/ci.yml?branch=main&amp;style=flat-square&amp;label=tests" alt="Tests" height="20"></a>
  <a href="https://pypi.org/project/dynra/"><img src="https://img.shields.io/badge/pypi-v0.4.1-ea7233?style=flat-square&amp;logo=pypi&amp;logoColor=white" alt="PyPI v0.4.1" height="20"></a>
  <a href="LICENSE"><img src="https://img.shields.io/github/license/antlobach/Dynra?style=flat-square" alt="Apache-2.0 license" height="20"></a>
</p>

## What Dynra is

Dynra brings REPL-driven development to Python, for developers and AI agents.
You define functions or classes, evaluate them in their project modules, and
inspect the results in the same long-lived session.

Compatible updates reach existing imports and class instances without
restarting or discarding their data. Save verified definitions to source files
and continue with the next piece in the same process.

## Set up a project

Requires Python 3.10+ and `uv`. From your project root:

```bash
uv add --dev dynra
uv run dynra --port 0
```

Dynra uses the project's environment and discovers its modules. Port `0`
selects an available port. In the REPL, `in_md("your_package.module")` selects
a module. Start your application explicitly when you need its live state.
Some changes still require a restart. Use Dynra only for trusted local development.

## AI agents

Give your agent the [Dynra skill](.agents/skills/dynra/SKILL.md). It explains
live development, module navigation, class updates, persistence, and restart
limits.

The MCP server provides module and source inspection, live Python evaluation,
and tools to review, save, or revert changes:

```json
{
  "mcpServers": {
    "dynra": {
      "command": "uv",
      "args": ["run", "dynra-mcp"]
    }
  }
}
```

Configure your MCP client to run this command from the Python project folder.
Keep the Dynra runtime above running while the agent works.

## Emacs

Requires Emacs 27.1+. Clone this repository once:

```bash
git clone https://github.com/antlobach/Dynra.git
```

Add this to your Emacs configuration. Replace the path with your checkout's
absolute path:

```elisp
(add-to-list 'load-path "/path/to/Dynra/editors/emacs")
(require 'dynra)
(dynra-auto-mode 1)
```

Reload the configuration. Open a project Python file and press `C-c C-x j`
(`M-x dynra-jack-in`). Emacs starts Dynra for you or connects to its running
session. Use `C-x C-e` to evaluate a statement or `C-c C-c` for a function or
class. Results appear inline.

See the [Emacs guide](editors/emacs/README.md) for the remaining commands and settings.
