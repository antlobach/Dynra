"""Generated from specs/lifecycle.allium; do not hand-edit without re-propagating."""

import asyncio
import json
import sys
import threading
import types

import pytest

from dynra.lifecycle import (
    BindingKind,
    ChangeSet,
    LifecycleRuntime,
    ReconciliationRefused,
    TaskPolicy,
    TaskSupervisor,
)


class Adapter:
    def __init__(self, name, watches=(), dependencies=(), fail=None, log=None):
        self.name = name
        self.watches = watches
        self.dependencies = dependencies
        self.fail = fail
        self.log = log if log is not None else []
        self.live = object()

    async def discover(self, runtime):
        self.log.append((self.name, "discover"))
        if self.fail == "discover":
            raise RuntimeError("discover")
        return self.live

    async def quiesce(self, old):
        self.log.append((self.name, "quiesce", old))

    async def snapshot(self, old):
        return {"old": old}

    async def prepare(self, changes):
        replacement = {"name": self.name, "version": 2}
        self.log.append((self.name, "prepare", replacement))
        return replacement

    async def validate(self, replacement):
        self.log.append((self.name, "validate", replacement))
        if self.fail == "validate":
            raise RuntimeError("validate")

    async def commit(self, replacement):
        self.log.append((self.name, "commit", replacement))
        if self.fail == "commit":
            raise RuntimeError("commit")

    async def resume(self, component):
        self.log.append((self.name, "resume", component))

    async def stop(self, component):
        self.log.append((self.name, "stop", component))

    async def compensate(self, failure):
        self.log.append((self.name, "compensate"))


def test_affected_worker_preserves_unchanged_database():
    runtime = LifecycleRuntime()
    database = Adapter("database", ("pkg.db.*",))
    worker = Adapter("worker", ("pkg.worker.*",), ("database",))
    runtime.register_component(database, database.live)
    runtime.register_component(worker, worker.live)
    plan = runtime.impact_plan(ChangeSet(("pkg.worker.run",)))
    assert plan.affected_components == ("worker",)
    assert "database" in plan.preserved
    plan = runtime.impact_plan(ChangeSet(("pkg.db.query",)))
    assert plan.affected_components == ("database", "worker")


def test_async_adapter_activation_attaches_real_instances_atomically():
    async def scenario():
        runtime = LifecycleRuntime(
            adapters=(Adapter("ok"), Adapter("bad", fail="discover"))
        )
        with pytest.raises(RuntimeError, match="discover"):
            await runtime.activate_adapters()
        assert runtime.graph.components == {}
        assert runtime.holders == {}
        existing = Adapter("existing")
        runtime = LifecycleRuntime(adapters=(Adapter("bad", fail="discover"),))
        runtime.register_component(existing, existing.live)
        with pytest.raises(RuntimeError, match="discover"):
            await runtime.activate_adapters()
        assert runtime.graph.components["existing"].instance is existing.live
        assert runtime.holders["component:existing"].get() is existing.live

        good = Adapter("service")
        runtime = LifecycleRuntime(adapters=(good,))
        plan = await runtime.plan(ChangeSet(("unrelated.value",)))
        assert plan.safe
        assert runtime.graph.components["service"].instance is good.live
        assert runtime.holders["component:service"].get() is good.live

    asyncio.run(scenario())


def test_pre_swap_failure_resumes_all_old_and_disposes_prepared_replacements(tmp_path):
    async def scenario():
        log = []
        runtime = LifecycleRuntime(journal_dir=tmp_path)
        first = Adapter("first", ("pkg.*",), log=log)
        second = Adapter("second", ("pkg.*",), ("first",), fail="validate", log=log)
        runtime.register_component(first, "old-first")
        runtime.register_component(second, "old-second")
        with pytest.raises(RuntimeError, match="validate"):
            await runtime.reconcile(ChangeSet(("pkg.changed",)))
        assert runtime.holders["component:first"].get() == "old-first"
        assert runtime.holders["component:second"].get() == "old-second"
        assert ("first", "resume", "old-first") in log
        assert ("second", "resume", "old-second") in log
        assert any(
            row[0] == "first" and row[1] == "stop" and isinstance(row[2], dict)
            for row in log
        )
        assert runtime.journal_view()[-1]["recovery"] == "old-system-active"

    asyncio.run(scenario())


def test_atomic_generation_snapshot_never_exposes_mixed_components():
    runtime = LifecycleRuntime()
    first = runtime.register_holder("first", 1)
    second = runtime.register_holder("second", 1)
    seen = set()
    stop = threading.Event()

    def reader():
        while not stop.is_set():
            snapshot = runtime.snapshot()
            seen.add((snapshot["first"], snapshot["second"]))

    thread = threading.Thread(target=reader)
    thread.start()
    for value in range(2, 200):
        runtime.atomic_swap({"first": value, "second": value})
    stop.set()
    thread.join()
    assert all(left == right for left, right in seen)
    assert first.get() == second.get() == 199


def test_binding_kinds_commit_and_rollback_transactionally():
    runtime = LifecycleRuntime()
    preserved = object()
    registry = {"handler": 1}
    runtime.register_binding(
        "state", preserved, BindingKind.PRESERVED, watches=("pkg.*",)
    )
    runtime.register_binding(
        "setting",
        1,
        BindingKind.RECOMPUTE,
        watches=("pkg.*",),
        prepare=lambda changes: 2,
    )
    runtime.register_binding(
        "registry",
        registry,
        BindingKind.REGISTRY,
        watches=("pkg.*",),
        prepare=lambda changes: {"handler": 2},
        validate=lambda value: None,
    )
    component = Adapter("component", ("pkg.*",))
    runtime.register_component(component, component.live)
    runtime.register_binding(
        "component",
        component.live,
        BindingKind.COMPONENT,
        watches=("pkg.*",),
        component="component",
    )
    asyncio.run(runtime.reconcile(ChangeSet(("pkg.value",))))
    assert runtime.holders["binding:state"].get() is preserved
    assert runtime.holders["binding:setting"].get() == 2
    assert runtime.holders["binding:registry"].get() == {"handler": 2}
    assert (
        runtime.holders["binding:component"].get()
        is runtime.holders["component:component"].get()
    )

    old = runtime.snapshot()
    runtime.register_binding(
        "bad",
        1,
        BindingKind.RECOMPUTE,
        watches=("bad.*",),
        prepare=lambda changes: 2,
        validate=lambda value: (_ for _ in ()).throw(ValueError("bad")),
    )
    with pytest.raises(ValueError, match="bad"):
        asyncio.run(runtime.reconcile(ChangeSet(("bad.value",))))
    assert runtime.snapshot()["binding:bad"] == 1
    assert runtime.snapshot()["binding:state"] is old["binding:state"]
    runtime.register_binding(
        "bad-registry",
        {"handler": 1},
        BindingKind.REGISTRY,
        watches=("broken.*",),
        prepare=lambda changes: {"handler": 2},
        validate=lambda value: (_ for _ in ()).throw(ValueError("registry")),
    )
    with pytest.raises(ValueError, match="registry"):
        asyncio.run(runtime.reconcile(ChangeSet(("broken.value",))))
    assert runtime.holders["binding:bad-registry"].get() == {"handler": 1}


def test_restart_only_and_task_policies_are_in_plan():
    runtime = LifecycleRuntime()
    runtime.register_binding(
        "startup", object(), BindingKind.RESTART_ONLY, watches=("pkg.start",)
    )
    runtime.register_task(
        TaskSupervisor(
            "worker", lambda: asyncio.sleep(10), TaskPolicy.NEVER, watches=("pkg.work",)
        )
    )
    runtime.register_task(
        TaskSupervisor(
            "drain", lambda: asyncio.sleep(10), TaskPolicy.DRAIN, watches=("pkg.work",)
        )
    )
    runtime.register_task(
        TaskSupervisor(
            "rolling",
            lambda: asyncio.sleep(10),
            TaskPolicy.ROLLING,
            watches=("pkg.work",),
        )
    )
    plan = runtime.impact_plan(ChangeSet(("pkg.start", "pkg.work")))
    assert not plan.safe
    assert plan.requires_process_restart
    assert set(plan.restarted) == {"worker", "drain", "rolling"}
    reasons = {reason.get("task"): reason["reason"] for reason in plan.refusals}
    assert reasons["drain"] == "drain policy has no drain hook"
    assert reasons["rolling"] == "rolling policy has no readiness check"
    assert {reason.get("binding") for reason in plan.refusals} == {"startup", None}


def test_affected_task_restarts_through_lifecycle_and_is_journaled():
    async def worker():
        await asyncio.Event().wait()

    async def scenario():
        runtime = LifecycleRuntime()
        supervisor = TaskSupervisor(
            "worker", worker, TaskPolicy.IMMEDIATE, watches=("pkg.work",)
        )
        runtime.register_task(supervisor)
        old = supervisor.start()

        result = await runtime.reconcile(ChangeSet(("pkg.work",)))

        assert result["plan"]["restarted"] == ("worker",)
        assert old.cancelled()
        assert supervisor.task is not old
        assert any(
            phase.get("phase") == "task-restarted" and phase.get("task") == "worker"
            for phase in result["phases"]
        )
        supervisor.task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await supervisor.task

    asyncio.run(scenario())


def test_durable_journal_survives_new_runtime(tmp_path):
    runtime = LifecycleRuntime(journal_dir=tmp_path)
    runtime.register_component(Adapter("service", ("pkg.*",)), "old")
    asyncio.run(runtime.reconcile(ChangeSet(("pkg.value",))))
    files = tuple(tmp_path.glob("*.json"))
    assert files
    restored = LifecycleRuntime(journal_dir=tmp_path)
    assert restored.journal_view()[-1]["status"] == "committed"
    json.dumps(restored.journal_view())
    runtime.prune_journal(keep=0)
    assert runtime.journal_view() == ()
    assert tuple(tmp_path.glob("*.json")) == ()


def test_task_safe_point_timeout_and_rolling_readiness():
    async def worker():
        await asyncio.Event().wait()

    async def scenario():
        safe = TaskSupervisor(
            "safe", worker, TaskPolicy.SAFE_POINT, watches=("pkg.*",), timeout=0.01
        )
        safe.start()
        with pytest.raises(ReconciliationRefused, match="safe point"):
            await safe.restart()
        safe.task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await safe.task

        seen = []

        async def readiness(replacement):
            seen.append(replacement)
            await asyncio.sleep(0)

        rolling = TaskSupervisor(
            "rolling",
            worker,
            TaskPolicy.ROLLING,
            watches=("pkg.*",),
            readiness=readiness,
            timeout=0.1,
        )
        old = rolling.start()
        await rolling.restart()
        assert seen == [rolling.task]
        assert old.cancelled()
        rolling.task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await rolling.task

    asyncio.run(scenario())


def test_project_local_adapter_is_explicit_and_lazy(tmp_path, monkeypatch):
    (tmp_path / "local_adapter.py").write_text(
        "class Adapter:\n"
        "    name = 'local'\n"
        "    dependencies = ()\n"
        "    watches = ('pkg.*',)\n"
        "    async def discover(self, runtime): return {'live': True}\n"
        "adapter = Adapter()\n",
        encoding="utf-8",
    )
    (tmp_path / "pyproject.toml").write_text(
        "[tool.dynra]\nadapters = ['local_adapter:adapter']\n",
        encoding="utf-8",
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    from dynra.core import NamespaceManager

    manager = NamespaceManager(project_root=tmp_path)
    assert manager.project.config.adapters == ("local_adapter:adapter",)
    assert manager.lifecycle.graph.components == {}
    assert asyncio.run(manager.lifecycle.activate_adapters()) == ("local",)
    assert manager.lifecycle.holders["component:local"].get() == {"live": True}


def test_definition_patch_refuses_before_managed_component_becomes_stale(tmp_path):
    from IPython.core.interactiveshell import InteractiveShell

    from dynra.core import NamespaceManager

    manager = NamespaceManager(project_root=tmp_path)
    module = manager.create_md("managed_patch")
    shell = InteractiveShell()
    manager.shell = shell
    manager._init_ipython_ns(module.__dict__)
    shell.user_ns = module.__dict__
    shell.user_module = module
    manager.current_name = module.__name__
    assert manager.execute_code("def value():\n    return 1\n").success
    original = module.value
    manager.lifecycle.register_component(
        Adapter("service", ("managed_patch.value",)), object()
    )

    result = manager.execute_code("def value():\n    return 2\n")

    assert not result.success
    assert isinstance(result.error, ReconciliationRefused)
    assert result.lifecycle_plan["affected_components"] == ("service",)
    assert module.value is original
    assert module.value() == 1


def test_incompatible_direct_holder_is_reported_in_lifecycle_plan(tmp_path):
    class Shell:
        def __init__(self, module):
            self.user_module = module
            self.user_ns = module.__dict__

    from dynra.core import NamespaceManager

    manager = NamespaceManager(project_root=tmp_path)
    module = manager.create_md("held_patch")
    manager.shell = Shell(module)
    exec(  # noqa: S102
        "async def value():\n    return 1\n", module.__dict__
    )
    original = module.value
    holder = [original]

    manager.pre_execute()
    exec("def value():\n    return 2\n", module.__dict__)  # noqa: S102
    manager.post_execute()
    plan = manager.lifecycle.impact_plan(ChangeSet(("held_patch.value",)))

    assert plan.safe is False
    assert plan.unmanaged_holders
    assert holder[0] is module.value is original


def test_definition_and_component_failure_roll_back_together(tmp_path):
    from IPython.core.interactiveshell import InteractiveShell

    from dynra.core import NamespaceManager

    async def scenario():
        shell = InteractiveShell()
        manager = NamespaceManager(shell=shell, project_root=tmp_path)
        module = manager.create_md("transaction_patch")
        manager.in_md(module.__name__)
        assert manager.execute_code("async def value():\n    return 1\n").success
        original = module.value
        aliases = types.ModuleType("transaction_aliases")
        aliases.current = original
        sys.modules[aliases.__name__] = aliases
        del original
        adapter = Adapter("service", ("transaction_patch.value",), fail="commit")
        manager.lifecycle.register_component(adapter, adapter.live)
        changes = ChangeSet(
            ("transaction_patch.value",),
            source_before={
                "transaction_patch.value": "async def value():\n    return 1\n"
            },
            source_after={"transaction_patch.value": "async def value():\n    return 2\n"},
        )

        manager._track_lifecycle_holders(changes)
        preflight = manager.lifecycle.impact_plan(changes)
        assert preflight.safe, preflight.unmanaged_holders
        with pytest.raises(RuntimeError, match="commit"):
            await manager.reconcile_lifecycle_changes(changes)

        assert module.value is aliases.current
        assert await module.value() == 1
        assert manager.lifecycle.holders["component:service"].get() is adapter.live
        phases = manager.lifecycle.journal_view()[-1]["phases"]
        assert any(phase["phase"] == "definitions-rolled-back" for phase in phases)
        sys.modules.pop(aliases.__name__, None)

    asyncio.run(scenario())


def test_multi_component_swap_without_quiescence_refuses():
    class NoQuiesce(Adapter):
        quiesce = None

    async def scenario():
        runtime = LifecycleRuntime()
        runtime.register_component(NoQuiesce("first", ("pkg.value",)), object())
        runtime.register_component(
            NoQuiesce("second", ("pkg.value",), ("first",)), object()
        )

        with pytest.raises(ReconciliationRefused, match="quiesce"):
            await runtime.reconcile(ChangeSet(("pkg.value",)))
        assert runtime.journal_view()[-1]["recovery"] == "old-system-active"

    asyncio.run(scenario())


def test_explicit_plan_collects_core_holder_evidence_before_mutation(tmp_path):
    from dynra.core import NamespaceManager

    manager = NamespaceManager(project_root=tmp_path)
    module = manager.create_md("planned_holder")
    exec(  # noqa: S102
        "async def value():\n    return 1\n", module.__dict__
    )
    holder = (module.value,)
    changes = ChangeSet(
        ("planned_holder.value",),
        source_after={"planned_holder.value": "def value():\n    return 2\n"},
    )

    plan = asyncio.run(manager.plan_lifecycle_changes(changes))

    assert plan.safe is False
    assert any("tuple" in evidence for evidence in plan.unmanaged_holders)
    assert holder[0] is module.value


def test_later_task_prepare_failure_leaves_all_old_tasks_running():
    async def worker():
        await asyncio.Event().wait()

    async def scenario():
        runtime = LifecycleRuntime()
        first = TaskSupervisor(
            "first", worker, TaskPolicy.IMMEDIATE, watches=("pkg.work",)
        )
        second = TaskSupervisor(
            "second",
            worker,
            TaskPolicy.SAFE_POINT,
            watches=("pkg.work",),
            timeout=0.01,
        )
        runtime.register_task(first)
        runtime.register_task(second)
        first_old = first.start()
        second_old = second.start()

        with pytest.raises(ReconciliationRefused, match="safe point"):
            await runtime.reconcile(ChangeSet(("pkg.work",)))

        assert first.task is first_old
        assert second.task is second_old
        assert not first_old.done()
        assert not second_old.done()
        first_old.cancel()
        second_old.cancel()
        await asyncio.gather(first_old, second_old, return_exceptions=True)

    asyncio.run(scenario())


def test_successful_definition_response_includes_lifecycle_plan(tmp_path):
    from IPython.core.interactiveshell import InteractiveShell

    from dynra.core import NamespaceManager

    shell = InteractiveShell()
    manager = NamespaceManager(shell=shell, project_root=tmp_path)
    module = manager.create_md("planned_response")
    manager.in_md(module.__name__)

    result = manager.execute_code("def value():\n    return 1\n")

    assert result.success
    assert result.lifecycle_plan["safe"] is True
    assert result.lifecycle_plan["changed"] == ("planned_response.value",)


def test_missing_adapter_semantics_refuse_before_mutation():
    class IncompleteAdapter:
        name = "incomplete"
        watches = ("pkg.value",)
        dependencies = ()

    async def scenario():
        runtime = LifecycleRuntime()
        runtime.register_component(IncompleteAdapter(), object())
        changes = ChangeSet(("pkg.value",))
        plan = runtime.impact_plan(changes)
        assert not plan.safe
        assert {item["operation"] for item in plan.refusals} == {
            "quiesce",
            "prepare",
            "validate",
            "commit",
            "compensate",
            "resume",
        }
        applied = False

        def apply():
            nonlocal applied
            applied = True

        with pytest.raises(ReconciliationRefused):
            await runtime.reconcile(changes, apply=apply, rollback=lambda: None)
        assert not applied

    asyncio.run(scenario())


def test_adapter_activation_validates_dependencies_after_batch_discovery():
    async def scenario():
        database = Adapter("database")
        worker = Adapter("worker", dependencies=("database",))
        runtime = LifecycleRuntime(adapters=(worker, database))
        assert await runtime.activate_adapters() == ("worker", "database")
        assert set(runtime.graph.components) == {"database", "worker"}

    asyncio.run(scenario())


def test_concurrent_journals_never_overwrite_transaction_files(tmp_path):
    async def scenario():
        first = LifecycleRuntime(journal_dir=tmp_path)
        second = LifecycleRuntime(journal_dir=tmp_path)
        await asyncio.gather(
            first.reconcile(ChangeSet(("pkg.first",))),
            second.reconcile(ChangeSet(("pkg.second",))),
        )

    asyncio.run(scenario())
    paths = list(tmp_path.glob("lifecycle-*.json"))
    assert len(paths) == 2
    assert len({json.loads(path.read_text())["transaction_id"] for path in paths}) == 2


def test_drain_restart_resumes_admission_after_commit():
    async def worker():
        await asyncio.Event().wait()

    async def scenario():
        events = []

        def drain():
            events.append("drain")

            def resume():
                events.append("resume")

            return resume

        supervisor = TaskSupervisor(
            "worker",
            worker,
            TaskPolicy.DRAIN,
            watches=("pkg.worker",),
            drain=drain,
        )
        supervisor.start()
        try:
            await supervisor.restart()
            assert events == ["drain", "resume"]
        finally:
            supervisor.task.cancel()
            await asyncio.gather(supervisor.task, return_exceptions=True)

    asyncio.run(scenario())


def test_rolling_readiness_receives_live_replacement_and_rejects_early_exit():
    async def scenario():
        seen = []

        async def running_worker():
            await asyncio.Event().wait()

        async def ready(replacement):
            seen.append(replacement)
            await asyncio.sleep(0)

        supervisor = TaskSupervisor(
            "rolling",
            running_worker,
            TaskPolicy.ROLLING,
            readiness=ready,
        )
        old = supervisor.start()
        prepared = await supervisor.prepare_restart()
        assert seen == [prepared.replacement]
        await supervisor.commit_restart(prepared)
        supervisor.task.cancel()
        await asyncio.gather(supervisor.task, return_exceptions=True)
        assert old.done()

        async def exits():
            return None

        async def never_ready(replacement):
            await asyncio.Event().wait()

        failing = TaskSupervisor(
            "exits", exits, TaskPolicy.ROLLING, readiness=never_ready
        )
        with pytest.raises(ReconciliationRefused, match="exited before readiness"):
            await failing.prepare_restart()

    asyncio.run(scenario())


def test_concurrent_reconciliations_are_serialized():
    async def scenario():
        entered = asyncio.Event()
        release = asyncio.Event()
        starts = []
        external = {}

        class SerializedAdapter(Adapter):
            async def prepare(self, changes):
                label = changes.definitions[0]
                starts.append(label)
                if label == "first":
                    entered.set()
                    await release.wait()
                return label

            async def commit(self, replacement):
                external["value"] = replacement

        runtime = LifecycleRuntime()
        adapter = SerializedAdapter("service", ("*",))
        runtime.register_component(adapter, adapter.live)
        first = asyncio.create_task(runtime.reconcile(ChangeSet(("first",))))
        await entered.wait()
        second = asyncio.create_task(runtime.reconcile(ChangeSet(("second",))))
        await asyncio.sleep(0)
        assert starts == ["first"]
        release.set()
        await asyncio.gather(first, second)
        assert starts == ["first", "second"]
        assert external["value"] == "second"
        assert runtime.graph.components["service"].instance == "second"

    asyncio.run(scenario())


def test_shared_live_resource_serializes_distinct_runtimes():
    async def scenario():
        entered = asyncio.Event()
        release = asyncio.Event()
        starts = []
        resource = object()
        external = {}

        class SharedAdapter(Adapter):
            async def prepare(self, changes):
                label = changes.definitions[0]
                starts.append(label)
                if label == "first":
                    entered.set()
                    await release.wait()
                return label

            def publication(self, replacement):
                return resource

            async def commit(self, replacement):
                external["value"] = replacement

        first_runtime = LifecycleRuntime()
        second_runtime = LifecycleRuntime()
        first_runtime.register_component(
            SharedAdapter("service", ("*",)), resource
        )
        second_runtime.register_component(
            SharedAdapter("service", ("*",)), resource
        )
        first = asyncio.create_task(
            first_runtime.reconcile(ChangeSet(("first",)))
        )
        await entered.wait()
        second = asyncio.create_task(
            second_runtime.reconcile(ChangeSet(("second",)))
        )
        await asyncio.sleep(0)
        assert starts == ["first"]
        release.set()
        await asyncio.gather(first, second)
        assert starts == ["first", "second"]
        assert external["value"] == "second"

    asyncio.run(scenario())


def test_definitions_remain_old_while_replacements_prepare(tmp_path):
    from IPython.core.interactiveshell import InteractiveShell

    from dynra.core import NamespaceManager

    async def scenario():
        shell = InteractiveShell()
        manager = NamespaceManager(shell=shell, project_root=tmp_path)
        module = manager.create_md("staged_visibility")
        manager.in_md(module.__name__)
        assert manager.execute_code("def value():\n    return 'old'\n").success
        entered = asyncio.Event()
        release = asyncio.Event()

        class BlockingAdapter(Adapter):
            async def prepare(self, changes):
                staged = changes.staged_definitions[
                    "staged_visibility.value"
                ]
                assert staged() == "new"
                entered.set()
                await release.wait()
                return object()

        adapter = BlockingAdapter(
            "service", ("staged_visibility.value",)
        )
        manager.lifecycle.register_component(adapter, adapter.live)
        changes = ChangeSet(
            ("staged_visibility.value",),
            source_before={
                "staged_visibility.value": "def value():\n    return 'old'\n"
            },
            source_after={
                "staged_visibility.value": "def value():\n    return 'new'\n"
            },
        )
        transaction = asyncio.create_task(
            manager.reconcile_lifecycle_changes(changes)
        )
        await entered.wait()
        assert module.value() == "old"
        release.set()
        await transaction
        assert module.value() == "new"

    asyncio.run(scenario())


def test_zero_argument_rolling_readiness_refuses_during_planning():
    async def worker():
        await asyncio.Event().wait()

    runtime = LifecycleRuntime()
    runtime.register_task(
        TaskSupervisor(
            "rolling",
            worker,
            TaskPolicy.ROLLING,
            watches=("pkg.worker",),
            readiness=lambda: asyncio.sleep(0),
        )
    )
    plan = runtime.impact_plan(ChangeSet(("pkg.worker",)))
    assert not plan.safe
    assert "replacement task" in plan.refusals[0]["reason"]


def test_new_definition_does_not_retarget_unrelated_none_values(tmp_path):
    from IPython.core.interactiveshell import InteractiveShell

    from dynra.core import NamespaceManager

    async def scenario():
        manager = NamespaceManager(
            shell=InteractiveShell(), project_root=tmp_path
        )
        module = manager.create_md("new_definition")
        module.unrelated = None
        module.values = [None]
        changes = ChangeSet(
            ("new_definition.created",),
            source_after={
                "new_definition.created": "def created():\n    return 42\n"
            },
        )
        await manager.reconcile_lifecycle_changes(changes)
        assert module.created() == 42
        assert module.unrelated is None
        assert module.values == [None]

    asyncio.run(scenario())


def test_concurrent_manager_failure_preserves_prior_commit(tmp_path):
    from IPython.core.interactiveshell import InteractiveShell

    from dynra.core import NamespaceManager

    async def scenario():
        manager = NamespaceManager(
            shell=InteractiveShell(), project_root=tmp_path
        )
        module = manager.create_md("serialized_definition")
        manager.in_md(module.__name__)
        assert manager.execute_code("def value():\n    return 0\n").success
        entered = asyncio.Event()
        release = asyncio.Event()

        class OrderedAdapter(Adapter):
            async def prepare(self, changes):
                source = changes.source_after[
                    "serialized_definition.value"
                ]
                if "return 1" in source:
                    entered.set()
                    await release.wait()
                    return 1
                return 2

            async def validate(self, replacement):
                if replacement == 2:
                    raise RuntimeError("second validation failed")

        adapter = OrderedAdapter(
            "service", ("serialized_definition.value",)
        )
        manager.lifecycle.register_component(adapter, adapter.live)
        first_changes = ChangeSet(
            ("serialized_definition.value",),
            source_after={
                "serialized_definition.value": "def value():\n    return 1\n"
            },
        )
        second_changes = ChangeSet(
            ("serialized_definition.value",),
            source_after={
                "serialized_definition.value": "def value():\n    return 2\n"
            },
        )
        first = asyncio.create_task(
            manager.reconcile_lifecycle_changes(first_changes)
        )
        await entered.wait()
        second = asyncio.create_task(
            manager.reconcile_lifecycle_changes(second_changes)
        )
        release.set()
        await first
        with pytest.raises(RuntimeError, match="second validation failed"):
            await second
        assert module.value() == 1

    asyncio.run(scenario())


def test_multi_definition_publication_refuses_before_mutation(tmp_path):
    from IPython.core.interactiveshell import InteractiveShell

    from dynra.core import NamespaceManager

    async def scenario():
        manager = NamespaceManager(
            shell=InteractiveShell(), project_root=tmp_path
        )
        module = manager.create_md("isolated_batch")
        module.first = "old-first"
        module.second = "old-second"
        changes = ChangeSet(
            ("isolated_batch.first", "isolated_batch.second"),
            source_after={
                "isolated_batch.first": "first = 'new-first'\n",
                "isolated_batch.second": "second = 'new-second'\n",
            },
        )
        plan = await manager.plan_lifecycle_changes(changes)
        assert not plan.safe
        assert "cannot atomically observe" in str(plan.refusals)
        with pytest.raises(
            ReconciliationRefused, match="cannot atomically observe"
        ):
            await manager.reconcile_lifecycle_changes(changes)
        assert (module.first, module.second) == (
            "old-first",
            "old-second",
        )

    asyncio.run(scenario())


def test_definition_time_side_effect_refuses_without_leaking(tmp_path):
    from IPython.core.interactiveshell import InteractiveShell

    from dynra.core import NamespaceManager

    async def scenario():
        manager = NamespaceManager(
            shell=InteractiveShell(), project_root=tmp_path
        )
        module = manager.create_md("isolated_default")
        module.events = []
        module.value = lambda: "old"
        changes = ChangeSet(
            ("isolated_default.value",),
            source_after={
                "isolated_default.value": (
                    "def value(marker=events.append('leaked')):\n"
                    "    return 'new'\n"
                )
            },
        )
        plan = await manager.plan_lifecycle_changes(changes)
        assert not plan.safe
        assert "definition-time expression" in str(plan.refusals)
        with pytest.raises(
            ReconciliationRefused, match="definition-time expression"
        ):
            await manager.reconcile_lifecycle_changes(changes)
        assert module.events == []
        assert module.value() == "old"

    asyncio.run(scenario())


def test_staging_preserves_postponed_annotation_semantics(tmp_path):
    import __future__

    from IPython.core.interactiveshell import InteractiveShell

    from dynra.core import NamespaceManager

    async def scenario():
        manager = NamespaceManager(
            shell=InteractiveShell(), project_root=tmp_path
        )
        module = manager.create_md("postponed_annotations")
        initial = (
            "class Resolved:\n"
            "    pass\n\n"
            "def value("
            "x: Missing['key'], y: Resolved['key']"
            ") -> Missing:\n"
            "    return y\n"
        )
        exec(  # noqa: S102
            compile(
                initial,
                "<postponed-annotations>",
                "exec",
                flags=__future__.annotations.compiler_flag,
                dont_inherit=True,
            ),
            module.__dict__,
        )
        original = module.value
        expected = dict(original.__annotations__)
        assert expected["x"] == "Missing['key']"
        assert expected["y"] == "Resolved['key']"
        assert expected["return"] == "Missing"
        changes = ChangeSet(
            ("postponed_annotations.value",),
            source_after={
                "postponed_annotations.value": (
                    "def value("
                    "x: Missing['key'], y: Resolved['key']"
                    ") -> Missing:\n"
                    "    return x\n"
                )
            },
        )
        plan = await manager.plan_lifecycle_changes(changes)
        assert plan.safe
        await manager.reconcile_lifecycle_changes(changes)
        assert module.value is original
        assert module.value.__annotations__ == expected

    asyncio.run(scenario())


def test_new_definition_ignores_imported_function_future_flags(tmp_path):
    import __future__

    from IPython.core.interactiveshell import InteractiveShell

    from dynra.core import NamespaceManager

    async def scenario():
        manager = NamespaceManager(
            shell=InteractiveShell(), project_root=tmp_path
        )
        future_module = manager.create_md("future_provider")
        exec(  # noqa: S102
            compile(
                "def helper(x: Missing):\n    return x\n",
                "<future-provider>",
                "exec",
                flags=__future__.annotations.compiler_flag,
                dont_inherit=True,
            ),
            future_module.__dict__,
        )
        module = manager.create_md("normal_annotations")
        module.helper = future_module.helper

        class Resolved:
            pass

        module.Resolved = Resolved
        changes = ChangeSet(
            ("normal_annotations.value",),
            source_after={
                "normal_annotations.value": (
                    "def value(x: Resolved):\n    return x\n"
                )
            },
        )
        plan = await manager.plan_lifecycle_changes(changes)
        assert plan.safe
        await manager.reconcile_lifecycle_changes(changes)
        assert module.value.__annotations__ == {"x": Resolved}

    asyncio.run(scenario())


def test_class_staging_refuses_without_running_subclass_hooks(tmp_path):
    from IPython.core.interactiveshell import InteractiveShell

    from dynra.core import NamespaceManager

    async def scenario():
        manager = NamespaceManager(
            shell=InteractiveShell(), project_root=tmp_path
        )
        module = manager.create_md("isolated_class")
        module.events = []

        class Parent:
            def __init_subclass__(cls):
                module.events.append(cls.__name__)

        module.Parent = Parent
        adapter = Adapter("class-service", ("isolated_class.Child",))
        manager.lifecycle.register_component(adapter, adapter.live)
        changes = ChangeSet(
            ("isolated_class.Child",),
            source_after={
                "isolated_class.Child": "class Child(Parent):\n    pass\n"
            },
        )
        plan = await manager.plan_lifecycle_changes(changes)
        assert not plan.safe
        assert "class construction" in str(plan.refusals)
        with pytest.raises(
            ReconciliationRefused, match="class construction"
        ):
            await manager.reconcile_lifecycle_changes(changes)
        assert module.events == []
        assert adapter.log == []
        assert not hasattr(module, "Child")

    asyncio.run(scenario())


def test_existing_decorator_does_not_run_during_failed_staging(tmp_path):
    from IPython.core.interactiveshell import InteractiveShell

    from dynra.core import NamespaceManager

    async def scenario():
        manager = NamespaceManager(
            shell=InteractiveShell(), project_root=tmp_path
        )
        module = manager.create_md("decorator_staging")
        manager.in_md(module.__name__)
        assert manager.execute_code(
            "calls = 0\n"
            "def decorate(fn):\n"
            "    global calls\n"
            "    calls += 1\n"
            "    return fn\n"
            "@decorate\n"
            "def value():\n"
            "    return 'old'\n"
        ).success
        assert module.calls == 1
        adapter = Adapter(
            "service",
            ("decorator_staging.value",),
            fail="validate",
        )
        manager.lifecycle.register_component(adapter, adapter.live)
        with pytest.raises(ReconciliationRefused, match="decorated definitions"):
            await manager.reconcile_lifecycle_changes(
                ChangeSet(
                    ("decorator_staging.value",),
                    source_after={
                        "decorator_staging.value": (
                            "@decorate\n"
                            "def value():\n"
                            "    return 'new'\n"
                        )
                    },
                )
            )
        assert module.calls == 1
        assert module.value() == "old"

    asyncio.run(scenario())


def test_unhashable_resource_mutex_lease_is_released():
    from dynra.lifecycle import _UNHASHABLE_RESOURCE_MUTEXES

    async def scenario():
        before = dict(_UNHASHABLE_RESOURCE_MUTEXES)
        runtime = LifecycleRuntime()
        adapter = Adapter("service", ("pkg.value",))
        runtime.register_component(adapter, {"version": 1})
        await runtime.reconcile(ChangeSet(("pkg.value",)))
        assert _UNHASHABLE_RESOURCE_MUTEXES == before

    asyncio.run(scenario())


def test_cancelled_quiescence_always_resumes_registered_component():
    async def scenario():
        entered = asyncio.Event()
        blocked = asyncio.Event()
        admission = {"open": True}

        class CancelledAdapter(Adapter):
            async def quiesce(self, old):
                admission["open"] = False
                entered.set()
                await blocked.wait()

            async def resume(self, component):
                admission["open"] = True

        runtime = LifecycleRuntime()
        adapter = CancelledAdapter("service", ("pkg.value",))
        runtime.register_component(adapter, adapter.live)
        transaction = asyncio.create_task(
            runtime.reconcile(ChangeSet(("pkg.value",)))
        )
        await entered.wait()
        transaction.cancel()
        with pytest.raises(asyncio.CancelledError):
            await transaction
        assert admission["open"]

    asyncio.run(scenario())



def test_cancelled_mutex_grant_does_not_deadlock_next_reconcile():
    from dynra.lifecycle import _AsyncMutex

    async def scenario():
        mutex = _AsyncMutex()
        await mutex.acquire()
        waiter = asyncio.create_task(mutex.acquire())
        await asyncio.sleep(0)
        mutex.release()
        asyncio.get_running_loop().call_soon(waiter.cancel)
        with pytest.raises(asyncio.CancelledError):
            await waiter
        await asyncio.wait_for(mutex.acquire(), 0.1)
        mutex.release()

    asyncio.run(scenario())


def test_rebound_class_peer_batch_refuses_without_partial_publication(tmp_path):
    from IPython.core.interactiveshell import InteractiveShell

    from dynra.core import NamespaceManager

    async def scenario():
        manager = NamespaceManager(
            shell=InteractiveShell(), project_root=tmp_path
        )
        module = manager.create_md("rebound_staging")
        manager.in_md(module.__name__)
        assert manager.execute_code(
            "class Parent:\n"
            "    pass\n\n"
            "class Base:\n"
            "    pass\n"
        ).success
        original = module.Base

        class ReboundAdapter(Adapter):
            async def prepare(self, changes):
                base = changes.staged_definitions[
                    "rebound_staging.Base"
                ]
                child = changes.staged_definitions[
                    "rebound_staging.Child"
                ]
                assert child.__bases__ == (base,)
                return object()

        adapter = ReboundAdapter("service", ("rebound_staging.*",))
        manager.lifecycle.register_component(adapter, adapter.live)
        with pytest.raises(ReconciliationRefused):
            await manager.reconcile_lifecycle_changes(
                ChangeSet(
                    ("rebound_staging.Base", "rebound_staging.Child"),
                    source_after={
                        "rebound_staging.Base": (
                            "class Base(Parent):\n    pass\n"
                        ),
                        "rebound_staging.Child": (
                            "class Child(Base):\n    pass\n"
                        ),
                    },
                )
            )
        assert module.Base is original
        assert not hasattr(module, "Child")

    asyncio.run(scenario())
