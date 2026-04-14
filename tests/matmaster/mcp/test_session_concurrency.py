"""Lock MCP SDK single-session concurrency assumptions.

This test uses in-memory streams to verify that one ClientSession can have two
requests in flight at once and still route responses correctly when the server
replies out of order.
"""

from __future__ import annotations

import anyio
from pydantic import BaseModel

from mcp import ClientSession
from mcp.shared.message import SessionMessage
from mcp.types import (
    CallToolRequest,
    CallToolRequestParams,
    JSONRPCMessage,
    JSONRPCRequest,
    JSONRPCResponse,
)


class _EchoResult(BaseModel):
    marker: str


async def test_out_of_order_responses_are_routed_by_request_id():
    client_read_send, client_read_recv = anyio.create_memory_object_stream(
        10
    )
    client_write_send, client_write_recv = anyio.create_memory_object_stream(
        10
    )

    results: dict[str, str] = {}
    received_requests: list[tuple[int, str]] = []

    async def server() -> None:
        while len(received_requests) < 2:
            message = await client_write_recv.receive()
            if isinstance(message, Exception):
                raise message

            root = message.message.root
            assert isinstance(root, JSONRPCRequest)
            marker = root.params["arguments"]["marker"]
            received_requests.append((root.id, marker))

        for request_id, marker in reversed(received_requests):
            response = JSONRPCResponse(
                jsonrpc="2.0",
                id=request_id,
                result={"marker": marker},
            )
            await client_read_send.send(
                SessionMessage(message=JSONRPCMessage(response))
            )

    session = ClientSession(client_read_recv, client_write_send)

    async with session:
        async with anyio.create_task_group() as task_group:
            task_group.start_soon(server)

            async def issue(marker: str) -> None:
                response = await session.send_request(
                    CallToolRequest(
                        params=CallToolRequestParams(
                            name="echo",
                            arguments={"marker": marker},
                        )
                    ),
                    _EchoResult,
                )
                results[marker] = response.marker

            task_group.start_soon(issue, "alpha")
            task_group.start_soon(issue, "beta")

    assert results == {"alpha": "alpha", "beta": "beta"}
