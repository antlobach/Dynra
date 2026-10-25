import asyncio

import pytest

from dynra.lifecycle import (
    BindingKind,
    ChangeSet,
    LifecycleRuntime,
    ReconciliationRefused,
    RuntimeGraph,
    TaskPolicy,
    TaskSupervisor,
    migrate_instances,
)


class Adapter:
    dependencies = ()
    watches = ("pkg.*",)

    def __init__(self, name="service", *, fail=None, log=None):
        self.name = name
        self.fail = fail
        self.log = log if log is not None else []

    async def quiesce(self, old):
        self.log.append(("quiesce", old))

    async def snapshot(self, old):
        return {"old": old}

    async def prepare(self, changes):
        self.log.append(("prepare", changes.definitions))
        if self.fail == "prepare":
            raise RuntimeError("prepare failed")
        return "new"

    async def migrate(self, replacement, snapshot):
        self.log.append(("migrate", replacement, snapshot))

    async def validate(self, replacement):
        self.log.append(("validate", replacement))
        if self.fail == "validate":
            raise RuntimeError("validate failed")

    async def commit(self, replacement):
        self.log.append(("commit", replacement))
        if self.fail == "commit":
            raise RuntimeError("commit failed")

    async def resume(self, component):
        self.log.append(("resume", component))

    async def stop(self, component):
        self.log.append(("stop", component))

    async def compensate(self, failure):
        self.log.append(("compensate", str(failure)))


def test_plan_is_deterministic_and_refuses_unmanaged_holders():
    runtime = LifecycleRuntime()
    runtime.register_component(Adapter(), "old")
    runtime.graph.track_holder("pkg.fn", "third_party.registry", managed=False)

    plan = runtime.impact_plan(ChangeSet(("pkg.fn",)))

    assert not plan.safe
    assert plan.requires_process_restart
    assert plan.affected_components == ("service",)
    assert plan.unmanaged_holders == ("pkg.fn: third_party.registry",)
    assert plan.refusals[0]["holder"] == "third_party.registry"


def test_component_dependency_order_and_cycles():
    graph = RuntimeGraph()
    database = Adapter("database")
    database.watches = ("pkg.db.*",)
    worker = Adapter("worker")
    worker.dependencies = ("database",)
    graph.register_component(type("R", (), {"name": "database", "dependencies": (), "watches": database.watches})())
    graph.register_component(type("R", (), {"name": "worker", "dependencies": ("database",), "watches": ("pkg.*",)})())
    assert graph.order(("worker",)) == ("database", "worker")

    cyclic = RuntimeGraph()
    cyclic._components = {
        "a": type("R", (), {"dependencies": ("b",)})(),
        "b": type("R", (), {"dependencies": ("a",)})(),
    }
    with pytest.raises(ValueError, match="cycle"):
        cyclic.order(("a",))


def test_successful_reconciliation_swaps_then_retires_old_component():
    runtime = LifecycleRuntime()
    adapter = Adapter()
    runtime.register_component(adapter, "old")

    result = asyncio.run(runtime.reconcile(ChangeSet(("pkg.fn",), {"pkg.fn": "a"}, {"pkg.fn": "b"})))

    assert result["status"] == "committed"
    assert result["recovery"] == "new-system-committed"
    assert runtime.holders["component:service"].get() == "new"
    assert adapter.log[-1] == ("stop", "old")
    assert runtime.journal_view()[0]["source_hashes"]["pkg.fn"]["before"]


@pytest.mark.parametrize("failure", ["prepare", "validate"])
def test_pre_swap_failure_leaves_old_component_active(failure):
    runtime = LifecycleRuntime()
    runtime.register_component(Adapter(fail=failure), "old")

    with pytest.raises(RuntimeError, match="failed"):
        asyncio.run(runtime.reconcile(ChangeSet(("pkg.fn",))))

    assert runtime.holders["component:service"].get() == "old"
    assert runtime.journal_view()[-1]["recovery"] == "old-system-active"


def test_post_swap_failure_compensates_and_restores_old_component():
    runtime = LifecycleRuntime()
    adapter = Adapter(fail="commit")
    runtime.register_component(adapter, "old")

    with pytest.raises(RuntimeError, match="commit failed"):
        asyncio.run(runtime.reconcile(ChangeSet(("pkg.fn",))))

    assert runtime.holders["component:service"].get() == "old"
    assert runtime.journal_view()[-1]["recovery"] == "old-system-restored"
    assert any(item[0] == "compensate" for item in adapter.log)


def test_missing_required_semantics_is_an_explicit_refusal():
    adapter = type("Minimal", (), {"name": "minimal", "dependencies": (), "watches": ("pkg.*",)})()
    runtime = LifecycleRuntime()
    runtime.register_component(adapter, "old")

    with pytest.raises(ReconciliationRefused, match="prepare") as error:
        asyncio.run(runtime.reconcile(ChangeSet(("pkg.fn",))))

    assert error.value.reasons[0]["component"] == "minimal"
    assert runtime.holders["component:minimal"].get() == "old"


def test_restart_only_binding_refuses_with_exact_boundary():
    runtime = LifecycleRuntime()
    runtime.classify("pkg.bootstrap", BindingKind.RESTART_ONLY)
    plan = runtime.impact_plan(ChangeSet(("pkg.bootstrap",)))
    assert not plan.safe
    assert plan.refusals[0]["definition"] == "pkg.bootstrap"


def test_instance_migration_rolls_back_complete_set():
    class State:
        def __init__(self, value):
            self.value = value

    first, second = State(1), State(2)

    def migrate(instance):
        instance.value += 10

    def validate(instance):
        if instance.value == 12:
            raise ValueError("invalid")

    with pytest.raises(ValueError):
        migrate_instances((first, second), migrate, validate)
    assert (first.value, second.value) == (1, 2)


def test_task_supervisor_requires_explicit_policy():
    async def scenario():
        async def worker():
            await asyncio.Event().wait()

        never = TaskSupervisor("worker", worker, TaskPolicy.NEVER)
        never.start()
        with pytest.raises(ReconciliationRefused, match="never"):
            await never.restart()
        never.task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await never.task

        immediate = TaskSupervisor("worker2", worker, TaskPolicy.IMMEDIATE)
        old = immediate.start()
        await immediate.restart()
        assert old.cancelled()
        assert immediate.task is not old
        immediate.task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await immediate.task

    asyncio.run(scenario())
