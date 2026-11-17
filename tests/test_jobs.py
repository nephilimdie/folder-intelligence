import asyncio

from folder_intelligence.assistant import Assistant
from folder_intelligence.jobs import CANCELLED, DONE, ERROR, JobManager
from folder_intelligence.memory import create_memory


def test_job_lifecycle_and_confirmation(sample_dir):
    async def go():
        jm = JobManager()
        changes = []
        jm.subscribe(lambda: changes.append(jm.version))
        a = Assistant(sample_dir, memory=create_memory("inmemory"), use_ai=False)
        child = a.fork()
        job = jm.start("cancella i duplicati", "chat", child, lambda j: child.handle("cancella i duplicati"))
        await job.task
        assert job.status == DONE and job.confirmation["kind"] == "dedupe"
        assert job.confirmation["question"].startswith("Confermi?")
        assert a.pending is None  # la sessione principale non è toccata dal fork
        assert changes
        bad = jm.start("boom", "x", None, lambda j: (_ for _ in ()).throw(RuntimeError("rotto")).__next__())
        try:
            await bad.task
        except Exception:
            pass
        assert bad.status == ERROR and "rotto" in bad.result_text

    asyncio.run(go())


def test_cancel_running_job():
    async def go():
        jm = JobManager()

        async def forever(job):
            while True:
                await asyncio.sleep(0.05)

        job = jm.start("lunga", "x", None, forever)
        await asyncio.sleep(0.1)
        assert [j.id for j in jm.active()] == [job.id]
        jm.cancel(job.id)
        await asyncio.wait_for(asyncio.shield(job.task), 5)
        assert job.status == CANCELLED and not jm.active()

    asyncio.run(go())
