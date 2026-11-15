"""
phone_gateway.py: the secure door a PHONE uses to reach VisionWorks.

A phone browser only opens its camera on https. The dashboard on this computer runs on plain http
(http://localhost:3000, fast and with no certificate warnings), so run_system.py starts this small gateway
next to it:

    https://<this computer's Wi-Fi address>:3443   ->   http://127.0.0.1:3000   (the phone's camera page)
                                         /api/...  ->   http://127.0.0.1:8001   (its live connection)

ONE secure address for both, because a phone treats every port as a separate site: two ports would mean
accepting the certificate warning twice. Each ordinary request is sent upstream with "Connection: close",
so every request picks its upstream by its own path; WebSocket upgrades are piped both ways untouched.

    python scripts/phone_gateway.py <cert.pem> <key.pem> [port]
"""
import asyncio
import ssl
import sys

FRONTEND = ("127.0.0.1", 3000)
BACKEND = ("127.0.0.1", 8001)
MAX_HEAD = 64 * 1024


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


async def handle(client_r, client_w):
    try:
        head = await client_r.readuntil(b"\r\n\r\n")
    except Exception:
        client_w.close()
        return
    if len(head) > MAX_HEAD:
        client_w.close()
        return
    lines = head.decode("latin-1").split("\r\n")
    request_line = lines[0]
    parts = request_line.split(" ")
    path = parts[1] if len(parts) > 1 else "/"
    headers = [h for h in lines[1:] if h]
    upgrade = any(h.lower().startswith("upgrade:") for h in headers)
    if not upgrade:
        # One request per connection, so the next request can go to the other upstream.
        headers = [h for h in headers if not h.lower().startswith(("connection:", "keep-alive:"))]
        headers.append("Connection: close")
    target = BACKEND if path.startswith("/api/") else FRONTEND
    try:
        up_r, up_w = await asyncio.open_connection(*target)
    except Exception:
        client_w.write(b"HTTP/1.1 502 Bad Gateway\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
        try:
            await client_w.drain()
        finally:
            client_w.close()
        return
    up_w.write(("\r\n".join([request_line] + headers) + "\r\n\r\n").encode("latin-1"))
    await up_w.drain()
    await asyncio.gather(pipe(client_r, up_w), pipe(up_r, client_w))


async def main(cert, key, port):
    ctx = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    ctx.load_cert_chain(cert, key)
    loop = asyncio.get_running_loop()
    # A phone that has not yet accepted the certificate drops the handshake: that is expected, not an error.
    loop.set_exception_handler(lambda _loop, _ctx: None)
    server = await asyncio.start_server(handle, "0.0.0.0", port, ssl=ctx)
    print(f"[GATEWAY] Phone entrance on https port {port}", flush=True)
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    try:
        asyncio.run(main(sys.argv[1], sys.argv[2], int(sys.argv[3]) if len(sys.argv) > 3 else 3443))
    except KeyboardInterrupt:
        pass
