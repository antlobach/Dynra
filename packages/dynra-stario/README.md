# dynra-stario

Development-only lifecycle adapter for using [Dynra](https://github.com/antlobach/Dynra) with a loaded Stario application.

```bash
uv add --dev dynra dynra-stario
uv run dynra
```

The adapter is discovered through the `dynra.adapters` entry-point group. Start Dynra before application bootstrap so it can track route and middleware registrations. It rebuilds that composition while preserving the loaded `stario.App`, listener, Relay, active requests, unaffected app tasks, and streams. Changed running tasks require an explicit Dynra `TaskSupervisor` policy or refuse because suspended coroutine frames cannot be rewritten. The adapter does not start or stop the application.

Application code does not import `dynra` or `dynra-stario`; removing these development dependencies leaves application startup unchanged.
