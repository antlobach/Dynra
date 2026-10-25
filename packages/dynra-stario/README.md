# dynra-stario

Development-only lifecycle adapter for using [Dynra](https://github.com/antlobach/Dynra) with a loaded Stario application.

```bash
uv add --dev dynra dynra-stario
uv run dynra
```

The adapter is discovered through the `dynra.adapters` entry-point group. Load it
before application route registration. In the development bootstrap, register a
side-effect-free route composer:

```python
from dynra_stario import compose_routes

compose_routes(app, build_routes)
```

The adapter resolves and replays `build_routes` after each definition update, so
routes can be added, removed, or reordered without replaying database, listener,
or task bootstrap side effects. It gates new request admission during
reconciliation, performs router work on the application's owner event loop, and
publishes the complete replacement router atomically. The loaded `stario.App`,
listener, Relay, active requests, unaffected tasks, and streams retain identity.
Changed running tasks require an explicit Dynra `TaskSupervisor` policy.

Production bootstrap can call `build_routes(app)` directly. The adapter does not
start or stop the application, and removing the development dependencies leaves
production startup unchanged.
