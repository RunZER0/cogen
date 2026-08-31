import asyncio

from app.agent_tools import as_async_tool


def test_async_tools_are_not_wrapped_into_unawaited_coroutines():
    async def tool() -> str:
        return "ok"

    wrapped = as_async_tool(tool)
    assert wrapped is tool
    assert asyncio.run(wrapped()) == "ok"
