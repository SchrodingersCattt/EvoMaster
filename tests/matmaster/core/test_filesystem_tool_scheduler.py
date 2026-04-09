import asyncio

from matmaster.core.tool_scheduler import ToolScheduler
from matmaster.types.tool_spec import ResourceClaim


async def _acquire_pair(claims1, claims2):
    scheduler = ToolScheduler()
    ticket1 = await scheduler.acquire(claims1, timeout=0.1)
    ticket2 = await scheduler.acquire(claims2, timeout=0.1)
    return ticket1, ticket2


def test_workspace_shared_reads_can_coexist() -> None:
    claims = (ResourceClaim(resource="workspace", mode="shared_read"),)
    ticket1, ticket2 = asyncio.run(_acquire_pair(claims, claims))
    assert ticket1 is not None
    assert ticket2 is not None


def test_workspace_exclusive_blocks_shared_read() -> None:
    scheduler = ToolScheduler()

    async def run() -> None:
        writer = await scheduler.acquire(
            (ResourceClaim(resource="workspace", mode="exclusive"),),
            timeout=0.1,
        )
        reader = await scheduler.acquire(
            (ResourceClaim(resource="workspace", mode="shared_read"),),
            timeout=0.1,
        )
        assert writer is not None
        assert reader is None

    asyncio.run(run())
