#!/usr/bin/env python3
"""A TLS terminator for loopback tests: accepts TLS on --listen, forwards the decrypted bytes to the plain relay on --target. 127.0.0.1 only."""
import argparse
import asyncio
import ssl


async def pipe(reader, writer):
    try:
        while True:
            data = await reader.read(65536)
            if not data:
                break
            writer.write(data)
            await writer.drain()
    except Exception:
        pass
    finally:
        try:
            writer.close()
        except Exception:
            pass


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--listen", type=int, required=True)
    ap.add_argument("--target", type=int, required=True)
    ap.add_argument("--cert", required=True)
    ap.add_argument("--key", required=True)
    a = ap.parse_args()
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(a.cert, a.key)

    async def handle(r, w):
        try:
            tr, tw = await asyncio.open_connection("127.0.0.1", a.target)
        except OSError:
            w.close()
            return
        await asyncio.gather(pipe(r, tw), pipe(tr, w))

    server = await asyncio.start_server(handle, "127.0.0.1", a.listen, ssl=ctx)
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
