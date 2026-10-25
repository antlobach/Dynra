import subprocess
import sys
import textwrap

from dynra.lifecycle import LifecycleRuntime


def test_process_death_after_swap_reports_incomplete_cleanup(tmp_path):
    script = textwrap.dedent(
        """
        import asyncio
        import os
        import sys
        from dynra.lifecycle import ChangeSet, LifecycleRuntime

        class Adapter:
            name = "service"
            dependencies = ()
            watches = ("app.handler",)
            async def quiesce(self, instance): return None
            async def snapshot(self, instance): return instance
            async def prepare(self, changes): return {"version": 2}
            async def migrate(self, replacement, snapshot): return None
            async def validate(self, replacement): return None
            async def commit(self, replacement): return None
            async def stop(self, instance): os._exit(73)
            async def compensate(self, failure): return None
            async def resume(self, instance): return None

        async def main():
            runtime = LifecycleRuntime(journal_dir=sys.argv[1])
            runtime.register_component(Adapter(), {"version": 1})
            await runtime.reconcile(ChangeSet(("app.handler",)))

        asyncio.run(main())
        """
    )

    completed = subprocess.run(
        [sys.executable, "-c", script, str(tmp_path)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 73

    recovered = LifecycleRuntime(journal_dir=tmp_path).journal_view()
    assert len(recovered) == 1
    assert recovered[0]["status"] == "recovery-required"
    assert recovered[0]["recovery"] == "cleanup-incomplete"
    assert any(phase["phase"] == "atomic-swap" for phase in recovered[0]["phases"])

    recovered_runtime = LifecycleRuntime(journal_dir=tmp_path)
    assert recovered_runtime.prune_journal(keep=0) == recovered
    assert tuple(tmp_path.glob("lifecycle-*.json"))
