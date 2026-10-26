import asyncio
from urllib.parse import urlsplit

from tests.tcp_proxy import TcpProxy, tcp_proxy


async def test_frozen_proxy_closes_active_connections_before_waiting_for_server() -> None:
    async def close_target(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        writer.close()
        await writer.wait_closed()

    target = await asyncio.start_server(close_target, "127.0.0.1", 0)
    port = target.sockets[0].getsockname()[1]
    proxy: TcpProxy | None = None
    try:
        async with asyncio.timeout(1):
            async with tcp_proxy(f"redis://127.0.0.1:{port}/0") as proxy:
                proxy.freeze()
                address = urlsplit(proxy.url)
                _, writer = await asyncio.open_connection(address.hostname, address.port)
                try:
                    # Let the accepted connection start while forwarding remains frozen.
                    await asyncio.sleep(0.05)
                finally:
                    writer.close()
                    await writer.wait_closed()
    finally:
        # Also clean up on the negative control's expected timeout.
        if proxy is not None:
            await proxy.close()
        target.close()
        await target.wait_closed()
