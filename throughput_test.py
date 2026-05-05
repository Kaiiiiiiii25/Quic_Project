"""
throughput_test.py — 量測 aioquic client 下載大檔案的吞吐量

直接呼叫 http3_client.py 的 main()，跑完後印出總時間和 Mbps。
適合在開/關 Network Link Conditioner 時各跑一次做對照。

用法（先 cd 到 QUIC_Project/，把這個檔放在跟 http3_client.py 同層）：

    # 不開網路模擬
    python throughput_test.py --ca-certs cert.pem \
        --algo reno --size 10MB --runs 3

    # 然後打開 Network Link Conditioner 選 100% Loss / 3G / etc.
    python throughput_test.py --ca-certs cert.pem \
        --algo reno --size 10MB --runs 3
"""

import argparse
import asyncio
import os
import statistics
import sys
import time
from urllib.parse import urlparse

# 確保 cwnd_monitor 在 client 端也跑（這樣 client 也會有 cwnd 紀錄）
import cwnd_monitor  # noqa: F401  (caller will install)

from aioquic.h3.connection import H3_ALPN
from aioquic.quic.configuration import QuicConfiguration
from aioquic.quic.logger import QuicFileLogger

# 重複利用同學寫的 client
from http3_client import HttpClient, perform_http_request
from aioquic.asyncio.client import connect


async def _one_run(url: str, ca_certs: str, algo: str,
                   qlog_dir: str | None) -> tuple[float, int]:
    cfg = QuicConfiguration(
        is_client=True,
        alpn_protocols=H3_ALPN,
        congestion_control_algorithm=algo,
    )
    cfg.load_verify_locations(ca_certs)
    if qlog_dir:
        os.makedirs(qlog_dir, exist_ok=True)
        cfg.quic_logger = QuicFileLogger(qlog_dir)

    parsed = urlparse(url)
    host = parsed.hostname
    port = parsed.port or 443

    t0 = time.perf_counter()
    bytes_received = 0

    async with connect(host, port, configuration=cfg,
                       create_protocol=HttpClient,
                       wait_connected=True) as client:
        events = await client.get(url)
        from aioquic.h3.events import DataReceived
        for ev in events:
            if isinstance(ev, DataReceived):
                bytes_received += len(ev.data)
        client.close()

    elapsed = time.perf_counter() - t0
    return elapsed, bytes_received


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ca-certs", required=True)
    parser.add_argument("--host", default="localhost")
    parser.add_argument("--port", type=int, default=4433)
    parser.add_argument("--size", default="10MB",
                        help="大小，例如 10MB / 50MB / 1GB")
    parser.add_argument("--algo", default="reno", choices=["reno", "cubic"])
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--qlog-dir", default="logs/")
    parser.add_argument("--cwnd-csv", default=None,
                        help="若給，會記 client 端 cwnd 變化")
    args = parser.parse_args()

    if args.cwnd_csv:
        cwnd_monitor.install(args.cwnd_csv)

    url = f"https://{args.host}:{args.port}/file/{args.size}"

    print(f"\n=== {args.algo.upper()} | {args.size} | runs={args.runs} ===")
    print(f"URL: {url}\n")

    durations: list[float] = []
    for i in range(args.runs):
        elapsed, n = asyncio.run(
            _one_run(url, args.ca_certs, args.algo, args.qlog_dir)
        )
        mbps = (n * 8) / elapsed / 1_000_000 if elapsed > 0 else 0
        print(f"  run {i+1}: {n:>10} bytes in {elapsed:6.2f}s  "
              f"→ {mbps:7.2f} Mbps")
        durations.append(elapsed)

    if len(durations) >= 2:
        print(f"\n  平均: {statistics.mean(durations):.2f}s  "
              f"標準差: {statistics.pstdev(durations):.2f}s")


if __name__ == "__main__":
    main()
