import asyncio

from repoagent import FakeModelClient, RepoAgent, SessionStore, WorkspaceContext
from repoagent.cron import CronSchedule, CronService
from repoagent.runtime_host import RuntimeHost


class Clock:
    def __init__(self, value):
        self.value = float(value)

    def __call__(self):
        return self.value


class Channel:
    name = "test"

    def __init__(self):
        self.sent = []

    async def send(self, chat_id, content, media=()):
        self.sent.append((chat_id, content, tuple(media)))


def test_cron_deduplicates_claims_and_reloads_across_instances(tmp_path):
    clock = Clock(100)
    path = tmp_path / "jobs.json"
    first = CronService(path, claim_ttl=10, clock=clock)
    second = CronService(path, claim_ttl=10, clock=clock)
    schedule = CronSchedule("at", at=100)

    job, duplicate = first.add("once", schedule, "work", channel="test", chat_id="room")
    same, duplicate_again = first.add(
        "duplicate", schedule, "work", channel="test", chat_id="room"
    )
    assert duplicate is False
    assert duplicate_again is True
    assert same.job_id == job.job_id
    assert len(second.list()) == 1

    claimed = first.claim_due("worker-a")
    assert [item.job_id for item in claimed] == [job.job_id]
    assert second.claim_due("worker-b") == ()
    completed = first.complete(job.job_id, "worker-a", {"status": "completed"})
    assert completed.enabled is False
    assert completed.run_count == 1
    assert second.list()[0].last_outcome["status"] == "completed"


def test_expired_claim_is_recovered_and_every_schedule_advances(tmp_path):
    clock = Clock(100)
    service = CronService(tmp_path / "jobs.json", claim_ttl=5, clock=clock)
    job, _ = service.add(
        "repeat",
        CronSchedule("every", every_seconds=10),
        "work",
        channel="test",
        chat_id="room",
    )
    clock.value = 110
    assert service.claim_due("worker-a")[0].job_id == job.job_id
    clock.value = 116
    assert service.claim_due("worker-b")[0].job_id == job.job_id
    completed = service.complete(job.job_id, "worker-b", {"status": "completed"})
    assert completed.enabled is True
    assert completed.next_run_at == 120


def test_cron_execution_uses_runtime_host_and_persists_delivery_outcome(tmp_path):
    async def scenario():
        clock = Clock(100)
        service = CronService(tmp_path / "jobs.json", clock=clock)
        job, _ = service.add(
            "once",
            CronSchedule("at", at=100),
            "scheduled work",
            channel="test",
            chat_id="room",
        )
        agent = RepoAgent(
            model_client=FakeModelClient(["<final>scheduled reply</final>"]),
            workspace=WorkspaceContext.build(tmp_path),
            session_store=SessionStore(tmp_path / ".repoagent" / "sessions"),
            approval_policy="never",
        )
        host = RuntimeHost(agent)
        channel = Channel()
        results = await service.run_due(host, {"test": channel}, owner="worker")
        await asyncio.sleep(0)
        assert results[0].job_id == job.job_id
        assert results[0].last_outcome["status"] == "completed"
        assert channel.sent == [("room", "scheduled reply", ())]
        await host.stop()

    asyncio.run(scenario())
