import asyncio
import sys
import types

import pytest

from dynra.lifecycle import ChangeSet, LifecycleRuntime, ReconciliationRefused

dynra_stario = pytest.importorskip("dynra_stario")
StarioAdapter = dynra_stario.StarioAdapter
compose_routes = dynra_stario.compose_routes
stario = pytest.importorskip("stario")
App = stario.App
responses = pytest.importorskip("stario.responses")
TestClient = pytest.importorskip("stario.testing").TestClient
Route = stario.Route


def _routes_from_module_globals(app):
    namespace = globals()
    app.add(
        namespace["Route"]("GET", "/value"),
        namespace["handler"],
    )


def _routes_from_default(app, handler):
    app.add(Route("GET", "/value"), handler)


def test_stario_app_identity_routes_tasks_and_streams_are_preserved():
    async def scenario():
        app = App()

        async def old_handler(context, writer):
            return None

        def middleware(handler):
            async def wrapped(context, writer):
                return await handler(context, writer)

            return wrapped

        app.add(Route("GET", "/value"), old_handler, middleware=(middleware,))
        original_routed_handler = app.find_handler("", "/value", "GET")[0]

        async def worker():
            await asyncio.Event().wait()

        task = app.create_task(worker(), name="preserved-worker")
        task_definition = (
            f"{task.get_coro().cr_frame.f_globals['__name__']}."
            f"{task.get_coro().cr_code.co_name}"
        )
        listener = object()
        relay = object()
        stream = object()
        app.listener = listener
        app.relay = relay
        app.streams = {stream}
        module = types.ModuleType("adapter_test_app")
        sys.modules[module.__name__] = module
        try:
            adapter = StarioAdapter()
            runtime = LifecycleRuntime(adapters=(adapter,))
            assert await runtime.activate_adapters() == ("stario-app",)
            plan = await runtime.plan(
                ChangeSet(
                    (
                        "adapter_test_app.old_handler",
                        "adapter_test_app.middleware",
                    )
                )
            )
            assert plan.affected_components == ("stario-app",)
            result = await runtime.reconcile(
                ChangeSet(
                    (
                        "adapter_test_app.old_handler",
                        "adapter_test_app.middleware",
                    )
                )
            )
            assert result["status"] == "committed"
            assert runtime.holders["component:stario-app"].get() is app
            rebuilt_routed_handler = app.find_handler("", "/value", "GET")[0]
            assert rebuilt_routed_handler is not original_routed_handler
            assert app.listener is listener
            assert app.relay is relay
            assert app.streams == {stream}
            assert task in app.tasks
            assert not task.done()
            assert not app.shutting_down
            with pytest.raises(
                ReconciliationRefused,
                match="running Stario task frames cannot be replaced",
            ):
                await runtime.reconcile(ChangeSet((task_definition,)))
            assert not task.done()
        finally:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            sys.modules.pop(module.__name__, None)

    asyncio.run(scenario())


def test_replayable_routes_discover_local_app_and_reconcile_on_owner_loop():
    async def scenario():
        module = types.ModuleType("replayable_stario_routes")

        async def old_handler(context, writer):
            responses.text(writer, "old")

        def routes(app):
            app.add(Route("GET", "/old"), old_handler)

        old_handler.__module__ = module.__name__
        old_handler.__qualname__ = "old_handler"
        routes.__module__ = module.__name__
        routes.__qualname__ = "routes"
        module.old_handler = old_handler
        module.routes = routes
        sys.modules[module.__name__] = module
        app = App()
        compose_routes(app, module.routes)
        original = app.find_handler("", "/old", "GET")[0]
        async with TestClient(app, owns_shutdown=False) as client:
            old_response = await client.get("/old")
            assert old_response.status_code == 200
            assert old_response.text == "old"
        try:
            runtime = LifecycleRuntime(adapters=(StarioAdapter(),))
            assert await runtime.activate_adapters() == ("stario-app",)

            async def new_handler(context, writer):
                responses.text(writer, "new")

            def updated_routes(candidate):
                candidate.add(Route("GET", "/new"), new_handler)

            new_handler.__module__ = module.__name__
            new_handler.__qualname__ = "new_handler"
            updated_routes.__module__ = module.__name__
            updated_routes.__qualname__ = "routes"
            module.new_handler = new_handler
            module.routes = updated_routes
            result = await asyncio.to_thread(
                lambda: asyncio.run(
                    runtime.reconcile(
                        ChangeSet(
                            (
                                "replayable_stario_routes.routes",
                                "replayable_stario_routes.new_handler",
                            )
                        )
                    )
                )
            )
            assert result["status"] == "committed"
            assert runtime.holders["component:stario-app"].get() is app
            assert app.find_handler("", "/new", "GET")[0] is module.new_handler
            assert app.find_handler("", "/old", "GET")[0] is not original
            async with TestClient(app, owns_shutdown=False) as client:
                new_response = await client.get("/new")
                old_response = await client.get("/old")
                assert new_response.status_code == 200
                assert new_response.text == "new"
                assert old_response.status_code == 404
        finally:
            sys.modules.pop(module.__name__, None)

    asyncio.run(scenario())


def test_cancelled_quiescence_reopens_request_admission():
    async def scenario():
        app = App()

        async def handler(context, writer):
            return None

        app.add(Route("GET", "/value"), handler)
        adapter = StarioAdapter()
        runtime = LifecycleRuntime(adapters=(adapter,))
        await runtime.activate_adapters()
        task = asyncio.create_task(adapter.quiesce(app))
        asyncio.get_running_loop().call_soon(task.cancel)
        with pytest.raises(asyncio.CancelledError):
            await task
        assert dynra_stario._control(app).gate.is_set()

    asyncio.run(scenario())


def test_unchanged_composer_uses_staged_changed_handler():
    async def scenario():
        module = types.ModuleType("unchanged_stario_composer")

        async def old_handler(context, writer):
            responses.text(writer, "old")

        module.Route = Route
        module.handler = old_handler
        module.routes = types.FunctionType(
            _routes_from_module_globals.__code__,
            module.__dict__,
            "routes",
        )
        for name in ("handler", "routes"):
            value = getattr(module, name)
            value.__module__ = module.__name__
            value.__qualname__ = name
        sys.modules[module.__name__] = module
        app = App()
        compose_routes(app, module.routes)
        try:
            runtime = LifecycleRuntime(adapters=(StarioAdapter(),))
            await runtime.activate_adapters()

            async def new_handler(context, writer):
                responses.text(writer, "new")

            new_handler.__module__ = module.__name__
            new_handler.__qualname__ = "handler"
            await runtime.reconcile(
                ChangeSet(
                    ("unchanged_stario_composer.handler",),
                    staged_definitions={
                        "unchanged_stario_composer.handler": new_handler
                    },
                )
            )
            assert app.find_handler("", "/value", "GET")[0] is new_handler
            async with TestClient(app, owns_shutdown=False) as client:
                response = await client.get("/value")
                assert response.status_code == 200
                assert response.text == "new"
        finally:
            sys.modules.pop(module.__name__, None)

    asyncio.run(scenario())


def test_unchanged_composer_retargets_staged_handler_default():
    async def scenario():
        module = types.ModuleType("default_stario_composer")

        async def old_handler(context, writer):
            responses.text(writer, "old")

        module.Route = Route
        module.handler = old_handler
        module.routes = types.FunctionType(
            _routes_from_default.__code__,
            module.__dict__,
            "routes",
            (old_handler,),
        )
        for name in ("handler", "routes"):
            value = getattr(module, name)
            value.__module__ = module.__name__
            value.__qualname__ = name
        sys.modules[module.__name__] = module
        app = App()
        compose_routes(app, module.routes)
        try:
            runtime = LifecycleRuntime(adapters=(StarioAdapter(),))
            await runtime.activate_adapters()

            async def new_handler(context, writer):
                responses.text(writer, "new")

            new_handler.__module__ = module.__name__
            new_handler.__qualname__ = "handler"
            await runtime.reconcile(
                ChangeSet(
                    ("default_stario_composer.handler",),
                    staged_definitions={
                        "default_stario_composer.handler": new_handler
                    },
                )
            )
            assert app.find_handler("", "/value", "GET")[0] is new_handler
        finally:
            sys.modules.pop(module.__name__, None)

    asyncio.run(scenario())
