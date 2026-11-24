"""Regression test: backend prints must not corrupt the stdio protocol channel.

The chatterbox (simulation) backend reports each step with print(). Over stdio,
stdout carries the JSON-RPC messages, so those lines landed in the protocol
stream. On Python 3.11 to 3.13 the client then dropped tool responses (with a
5 s timeout per call, the 3rd, 5th and 7th of 8 transfers timed out); on 3.10
every call returned, but the stray lines still reached the client. So the test,
which starts the server in a subprocess on chatterbox (no device), fails on a
call that never returns and on any line the client cannot parse.
"""

import asyncio
import os
import sys

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

START_TIMEOUT_S = 60  # the first start imports PyLabRobot; a busy machine needs the room
CALL_TIMEOUT_S = 30  # a dropped response never arrives, so any finite bound catches it
N_TRANSFERS = 8
# Above the sum of the per-step limits, so a slow step hits its own limit first.
TOTAL_TIMEOUT_S = 2 * START_TIMEOUT_S + N_TRANSFERS * CALL_TIMEOUT_S + 60


def test_stdio_transfers_all_return_on_a_clean_channel():
    stray = []

    async def on_message(message):
        if isinstance(message, Exception):
            stray.append(message)

    async def go():
        params = StdioServerParameters(
            command=sys.executable,
            args=["-m", "plr_mcp.server"],
            env={**os.environ, "PLR_MCP_BACKEND": "chatterbox"},
        )
        with open(os.devnull, "w") as errlog:
            async with stdio_client(params, errlog=errlog) as (read, write):
                async with ClientSession(read, write, message_handler=on_message) as session:
                    await asyncio.wait_for(session.initialize(), START_TIMEOUT_S)
                    setup = await asyncio.wait_for(
                        session.call_tool("plr_setup_deck", {}), START_TIMEOUT_S
                    )
                    assert setup.isError is False
                    for i in range(1, N_TRANSFERS + 1):
                        try:
                            result = await asyncio.wait_for(
                                session.call_tool(
                                    "plr_transfer",
                                    {"source": "A1:H1", "dest": "A12:H12", "volume": 20},
                                ),
                                CALL_TIMEOUT_S,
                            )
                        except asyncio.TimeoutError:
                            raise AssertionError(f"transfer {i} of {N_TRANSFERS} timed out")
                        assert result.isError is False, f"transfer {i} failed"
                        assert result.structuredContent["simulated"] is True

    asyncio.run(asyncio.wait_for(go(), TOTAL_TIMEOUT_S))
    assert not stray, (
        f"{len(stray)} non-JSON-RPC lines on the server's stdout; first: {stray[0]!r:.200}"
    )
