"""Fixed TCP relay to the natively installed Ollama app (macOS).

Docker on a Mac cannot use the GPU, so Ollama runs as a normal Mac app. Containers reach the
Mac through `host.docker.internal`, but that needs a network with an outside route. Instead of
giving the Gateway such a route, this relay is the only container on it. It forwards to exactly
one fixed address and nothing else, so the Gateway stays on internal networks only.

    python -m gateway.relay --listen 0.0.0.0:11434 --target host.docker.internal:11434
"""

from __future__ import annotations

import argparse
import asyncio


async def _pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    """Copy one direction; on end-of-stream pass the half-close on instead of closing both ways."""
    try:
        while data := await reader.read(65536):
            writer.write(data)
            await writer.drain()
        if writer.can_write_eof():
            writer.write_eof()
    except (ConnectionError, asyncio.CancelledError):
        pass


def make_handler(host: str, port: int):
    async def handle(client_r: asyncio.StreamReader, client_w: asyncio.StreamWriter) -> None:
        try:
            up_r, up_w = await asyncio.wait_for(asyncio.open_connection(host, port), timeout=10)
        except (OSError, asyncio.TimeoutError):
            client_w.close()
            return
        try:
            await asyncio.gather(_pipe(client_r, up_w), _pipe(up_r, client_w))
        finally:
            for w in (up_w, client_w):
                try:
                    w.close()
                except Exception:
                    pass
    return handle


def _addr(s: str) -> tuple[str, int]:
    host, _, port = s.rpartition(":")
    return host, int(port)


async def serve(listen: str, target: str) -> asyncio.base_events.Server:
    lh, lp = _addr(listen)
    th, tp = _addr(target)
    return await asyncio.start_server(make_handler(th, tp), lh, lp)


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--listen", default="0.0.0.0:11434")
    ap.add_argument("--target", default="host.docker.internal:11434")
    a = ap.parse_args(argv)

    async def run():
        server = await serve(a.listen, a.target)
        print(f"relay {a.listen} -> {a.target}", flush=True)
        async with server:
            await server.serve_forever()

    asyncio.run(run())


if __name__ == "__main__":
    main()
