"""
demo.py — 第二階段用的 ASGI app

提供三個 endpoint：
  GET  /                   → "Hello from QUIC Server!" (相容你同學原本的)
  GET  /file?size=10485760 → 回傳 size 個 bytes 的 payload (預設 10MB)
  GET  /file/<n>MB         → 例如 /file/5MB → 5MB；/file/100KB → 100KB

只有大檔案 endpoint 才能看出 cwnd 變化的差異。

server 端如果要同時開 cwnd 監控，把這段加到 http3_server.py 最頂端：

    import cwnd_monitor
    cwnd_monitor.install("logs/cwnd_server.csv")
"""

import re

_UNIT = {"B": 1, "KB": 1024, "MB": 1024 * 1024, "GB": 1024 * 1024 * 1024}


def _parse_size(s: str) -> int:
    m = re.fullmatch(r"\s*(\d+)\s*([KMG]?B)?\s*", s.upper())
    if not m:
        return 0
    n = int(m.group(1))
    unit = m.group(2) or "B"
    return n * _UNIT.get(unit, 1)


def _parse_query(qs: bytes) -> dict[str, str]:
    out: dict[str, str] = {}
    if not qs:
        return out
    for pair in qs.decode(errors="ignore").split("&"):
        if "=" in pair:
            k, v = pair.split("=", 1)
            out[k] = v
    return out


async def app(scope, receive, send):
    if scope["type"] != "http":
        return

    path: str = scope.get("path", "/")
    query = _parse_query(scope.get("query_string", b""))

    # ----- 大檔案 endpoint -----
    size_bytes = 0
    m = re.fullmatch(r"/file/(\d+(?:KB|MB|GB|B)?)", path, flags=re.IGNORECASE)
    if path == "/file":
        size_bytes = _parse_size(query.get("size", "10485760"))  # 預設 10MB
    elif m:
        size_bytes = _parse_size(m.group(1))

    if size_bytes > 0:
        await send({
            "type": "http.response.start",
            "status": 200,
            "headers": [
                (b"content-type", b"application/octet-stream"),
                (b"content-length", str(size_bytes).encode()),
            ],
        })
        # 一次送 64KB，讓 QUIC 真的需要排程很多封包
        chunk = b"\x00" * 65536
        remaining = size_bytes
        while remaining > 0:
            send_size = min(remaining, len(chunk))
            await send({
                "type": "http.response.body",
                "body": chunk[:send_size] if send_size < len(chunk) else chunk,
                "more_body": remaining - send_size > 0,
            })
            remaining -= send_size
        return

    # ----- 預設首頁 -----
    await send({
        "type": "http.response.start",
        "status": 200,
        "headers": [(b"content-type", b"text/plain")],
    })
    body = (
        b"Hello from QUIC Server!\n"
        b"Try:\n"
        b"  /file/10MB\n"
        b"  /file/100MB\n"
        b"  /file?size=5242880\n"
    )
    await send({"type": "http.response.body", "body": body})
