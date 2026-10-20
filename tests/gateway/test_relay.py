"""The macOS relay forwards bytes both ways to one fixed target."""
import asyncio

from gateway.relay import serve


def test_relay_round_trip():
    async def run():
        async def echo(r, w):
            w.write(b"pong:" + await r.read(100)); await w.drain(); w.close()
        target = await asyncio.start_server(echo, "127.0.0.1", 0)
        tport = target.sockets[0].getsockname()[1]
        relay = await serve("127.0.0.1:0", f"127.0.0.1:{tport}")
        rport = relay.sockets[0].getsockname()[1]
        r, w = await asyncio.open_connection("127.0.0.1", rport)
        w.write(b"ping"); await w.drain(); w.write_eof()
        got = await asyncio.wait_for(r.read(), 5)
        relay.close(); target.close()
        return got
    assert asyncio.run(run()) == b"pong:ping"


def test_relay_closes_when_target_down():
    async def run():
        relay = await serve("127.0.0.1:0", "127.0.0.1:1")
        r, w = await asyncio.open_connection("127.0.0.1", relay.sockets[0].getsockname()[1])
        got = await asyncio.wait_for(r.read(), 15)
        relay.close()
        return got
    assert asyncio.run(run()) == b""
