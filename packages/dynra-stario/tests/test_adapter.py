import asyncio
import sys
import types

import pytest

from dynra.lifecycle import ChangeSet, LifecycleRuntime, ReconciliationRefused

StarioAdapter = pytest.importorskip("dynra_stario").StarioAdapter
stario = pytest.importorskip("stario")
App = stario.App
Route = stario.Route


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
        module.app = app
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
