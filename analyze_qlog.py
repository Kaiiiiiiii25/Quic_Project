"""
analyze_qlog.py — 第三階段視覺化主程式

讀取 aioquic 產生的 qlog 檔，畫出四合一的對照圖：
  1. cwnd (擁塞視窗) 隨時間變化
  2. 吞吐量 (instantaneous throughput, 滑動窗口)
  3. RTT (smoothed_rtt + latest_rtt)
  4. 累計 loss 事件數

支援同時比較多個實驗（Reno vs Cubic、有/無丟包模擬等）。

用法：
  # 比較單一 qlog
  python analyze_qlog.py logs/baseline_reno/abc.qlog

  # 比較多組（自動依資料夾名分類）
  python analyze_qlog.py \\
      --label "Reno baseline"   logs/baseline_reno/*.qlog \\
      --label "Cubic baseline"  logs/baseline_cubic/*.qlog \\
      --label "Reno + 10% loss" logs/loss10_reno/*.qlog \\
      --label "Cubic + 10% loss" logs/loss10_cubic/*.qlog \\
      --output compare.png

  # 簡化版：每個 qlog 自動以路徑當 label
  python analyze_qlog.py --auto logs/**/*.qlog --output compare.png
"""

from __future__ import annotations
import argparse
import glob
import json
import os
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Optional

import matplotlib.pyplot as plt
import matplotlib.ticker as mticker


# -------------------------------------------------------------------
# qlog 解析
# -------------------------------------------------------------------

@dataclass
class Trace:
    """單一 qlog 解析結果。"""
    name: str = ""
    cc_algo: str = ""

    # 各 series 都用平行陣列存 (時間 ms, 值)
    t_cwnd: list[float] = field(default_factory=list)
    cwnd: list[float] = field(default_factory=list)

    t_ssthresh: list[float] = field(default_factory=list)
    ssthresh: list[float] = field(default_factory=list)

    t_rtt: list[float] = field(default_factory=list)
    smoothed_rtt: list[float] = field(default_factory=list)

    t_sent: list[float] = field(default_factory=list)
    sent_bytes: list[int] = field(default_factory=list)  # 累積

    t_recv: list[float] = field(default_factory=list)
    recv_bytes: list[int] = field(default_factory=list)  # 累積

    t_loss: list[float] = field(default_factory=list)


def _iter_events(qlog_path: str):
    """yield (relative_time_ms, name, data) for each event.

    aioquic 產生 qlog format=0.3：整檔是一個 JSON document，
      {"qlog_format": "JSON", "qlog_version": "0.3",
       "traces": [{"common_fields": {...}, "events": [...]}]}
    舊版 NDJSON 也支援（每行一個 event）。
    """
    with open(qlog_path, "r") as f:
        content = f.read()

    if not content.strip():
        return

    # 嘗試當作完整 JSON 解析（aioquic 0.3 格式）
    try:
        obj = json.loads(content)
    except json.JSONDecodeError:
        # NDJSON fallback — 每行一個 event
        for line in content.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            yield _parse_event(ev)
        return

    # qlog 0.3：events 在 traces[0].events
    traces = obj.get("traces") or []
    for trace in traces:
        for ev in trace.get("events", []):
            yield _parse_event(ev)
        return  # 只處理第一個 trace

    # 退路：頂層直接有 events（很罕見的格式）
    if "events" in obj and isinstance(obj["events"], list):
        for ev in obj["events"]:
            yield _parse_event(ev)


def _parse_event(ev):
    """將一個 event 標準化成 (time_ms, name, data)。

    支援兩種 schema：
    - draft-02: ev = {"time": ..., "name": "transport:packet_sent", "data": {...}}
    - 舊版陣列: ev = [time, "transport", "packet_sent", {...}]
    """
    if isinstance(ev, dict):
        t = ev.get("time", 0)
        name = ev.get("name", "")
        data = ev.get("data", {})
        if not name:
            cat = ev.get("category", "")
            evt = ev.get("event", "")
            name = f"{cat}:{evt}" if cat and evt else evt
        return float(t), name, data or {}
    if isinstance(ev, list) and len(ev) >= 4:
        t, cat, evt, data = ev[0], ev[1], ev[2], ev[3]
        return float(t), f"{cat}:{evt}", data or {}
    return 0.0, "", {}


def parse_qlog(path: str, name: Optional[str] = None) -> Trace:
    tr = Trace(name=name or os.path.basename(path))
    sent_total = 0
    recv_total = 0
    t0: Optional[float] = None

    for t, ename, data in _iter_events(path):
        if t0 is None:
            t0 = t
        rel_ms = t - t0

        # cwnd / rtt 都從 metrics_updated 來
        if ename.endswith(":metrics_updated") or ename == "metrics_updated":
            # aioquic 0.3 用 "cwnd"；舊版/其他實作可能用 "congestion_window"
            cw = data.get("cwnd", data.get("congestion_window"))
            if cw is not None:
                tr.t_cwnd.append(rel_ms)
                tr.cwnd.append(float(cw))
            if "ssthresh" in data:
                tr.t_ssthresh.append(rel_ms)
                tr.ssthresh.append(float(data["ssthresh"]))
            srtt = data.get("smoothed_rtt", data.get("latest_rtt"))
            if srtt is not None:
                tr.t_rtt.append(rel_ms)
                tr.smoothed_rtt.append(float(srtt))

        # sent / received bytes (拿 raw.length 或 header.packet_size)
        elif ename.endswith(":packet_sent") or ename == "packet_sent":
            sz = _packet_size(data)
            if sz > 0:
                sent_total += sz
                tr.t_sent.append(rel_ms)
                tr.sent_bytes.append(sent_total)

        elif ename.endswith(":packet_received") or ename == "packet_received":
            sz = _packet_size(data)
            if sz > 0:
                recv_total += sz
                tr.t_recv.append(rel_ms)
                tr.recv_bytes.append(recv_total)

        elif ename.endswith(":packet_lost") or ename == "packet_lost":
            tr.t_loss.append(rel_ms)

        elif ename.endswith(":parameters_set") and not tr.cc_algo:
            tr.cc_algo = data.get("congestion_control", "") or tr.cc_algo

    return tr


def _packet_size(data: dict) -> int:
    """從 packet_sent/received event 中萃取大小（bytes）。"""
    raw = data.get("raw", {})
    if isinstance(raw, dict) and "length" in raw:
        return int(raw["length"])
    if "packet_size" in data:
        return int(data["packet_size"])
    hdr = data.get("header", {})
    if isinstance(hdr, dict) and "packet_size" in hdr:
        return int(hdr["packet_size"])
    return 0


def merge_traces(traces: list[Trace], label: str) -> Trace:
    """把同 label 下的多個 qlog 合併（取累積總長度最長的當代表）。

    每次 throughput_test.py 跑一次就會產生一個 qlog；--runs 3 就有 3 個。
    為了不混在一起，這裡只取「資料量最大」的那個（通常就是真正成功跑完的那次）。
    """
    if not traces:
        return Trace(name=label)
    if len(traces) == 1:
        traces[0].name = label
        return traces[0]
    best = max(traces, key=lambda t: (t.sent_bytes[-1] if t.sent_bytes else 0))
    best.name = label
    return best


# -------------------------------------------------------------------
# 衍生指標：滑動窗口吞吐量
# -------------------------------------------------------------------

def compute_throughput(t_ms: list[float], cum_bytes: list[int],
                       window_ms: float = 200,
                       min_window_ms: float = 50,
                       min_bytes: int = 50_000) -> tuple[list[float], list[float]]:
    """從累積 bytes 算出滑動窗口的瞬時吞吐量 (Mbps)。

    為避免起始的「短窗口大數值」尖峰，要求每個窗口至少要有
      - min_window_ms 毫秒的時間跨度，或者
      - min_bytes 個 bytes 的樣本量
    都不滿足就跳過該點。
    """
    if not t_ms:
        return [], []
    out_t, out_v = [], []
    j = 0
    for i in range(len(t_ms)):
        while j < i and t_ms[i] - t_ms[j] > window_ms:
            j += 1
        dt_ms = t_ms[i] - t_ms[j]
        dB = cum_bytes[i] - cum_bytes[j]
        # 過濾「窗口太短或樣本太少」的點，避免假尖峰
        if dt_ms < min_window_ms and dB < min_bytes:
            continue
        if dt_ms <= 0:
            continue
        mbps = (dB * 8) / (dt_ms / 1000.0) / 1_000_000
        out_t.append(t_ms[i])
        out_v.append(mbps)
    return out_t, out_v


# -------------------------------------------------------------------
# 繪圖
# -------------------------------------------------------------------

# 設定中文字型（macOS 標配字型）
plt.rcParams["font.sans-serif"] = [
    "PingFang TC", "PingFang SC", "Heiti TC", "Arial Unicode MS",
    "Noto Sans CJK TC", "DejaVu Sans"
]
plt.rcParams["axes.unicode_minus"] = False


# 跨組固定配色 — 同條件不同演算法用同色系
COLOR_MAP = {
    # 給常見 label 預配色，沒有的會 fallback 到 cycle
    "reno baseline":     "#185FA5",  # 藍
    "cubic baseline":    "#1D9E75",  # 綠
    "reno + 10% loss":   "#993C1D",  # 紅褐
    "cubic + 10% loss":  "#854F0B",  # 琥珀
}
FALLBACK = ["#185FA5", "#1D9E75", "#993C1D", "#854F0B",
            "#534AB7", "#A32D2D", "#3B6D11", "#993556"]


def color_of(label: str, idx: int) -> str:
    return COLOR_MAP.get(label.lower().strip(), FALLBACK[idx % len(FALLBACK)])


def plot_compare(traces: list[Trace], output: str = "compare.png",
                 throughput_window_ms: float = 200) -> None:
    fig, axes = plt.subplots(2, 2, figsize=(14, 9), constrained_layout=True)
    ax_cwnd, ax_tp = axes[0, 0], axes[0, 1]
    ax_rtt, ax_loss = axes[1, 0], axes[1, 1]

    for i, tr in enumerate(traces):
        c = color_of(tr.name, i)

        # 1) cwnd
        if tr.cwnd:
            cwnd_kb = [v / 1024 for v in tr.cwnd]
            ax_cwnd.plot(tr.t_cwnd, cwnd_kb, color=c, label=tr.name,
                         linewidth=1.4, alpha=0.9)
            # loss 用稀疏的 × 標在曲線上（取樣到最多 30 個避免太密）
            if tr.t_loss and tr.t_cwnd:
                # 為每個 loss 時間點找最接近的 cwnd 值來定位 y
                import bisect
                stride = max(1, len(tr.t_loss) // 30)
                for k in range(0, len(tr.t_loss), stride):
                    tl = tr.t_loss[k]
                    j = bisect.bisect_left(tr.t_cwnd, tl)
                    j = min(j, len(tr.cwnd) - 1)
                    ax_cwnd.scatter([tl], [tr.cwnd[j] / 1024],
                                    color=c, marker="x", s=22,
                                    linewidths=1.0, zorder=5)

        # 2) throughput
        # 優先用 sent (server qlog) 否則用 recv (client qlog)
        if tr.sent_bytes:
            t_t, t_v = compute_throughput(tr.t_sent, tr.sent_bytes,
                                          window_ms=throughput_window_ms)
        elif tr.recv_bytes:
            t_t, t_v = compute_throughput(tr.t_recv, tr.recv_bytes,
                                          window_ms=throughput_window_ms)
        else:
            t_t, t_v = [], []
        if t_v:
            ax_tp.plot(t_t, t_v, color=c, label=tr.name, linewidth=1.4, alpha=0.9)

        # 3) RTT
        if tr.smoothed_rtt:
            ax_rtt.plot(tr.t_rtt, tr.smoothed_rtt, color=c, label=tr.name,
                        linewidth=1.4, alpha=0.9)

        # 4) cumulative loss
        if tr.t_loss:
            cum = list(range(1, len(tr.t_loss) + 1))
            ax_loss.step(tr.t_loss, cum, color=c, label=tr.name,
                         linewidth=1.4, where="post")
        else:
            ax_loss.plot([], [], color=c, label=f"{tr.name} (no loss)")

    ax_cwnd.set_title("Congestion Window 隨時間變化")
    ax_cwnd.set_xlabel("時間 (ms)")
    ax_cwnd.set_ylabel("cwnd (KB)")
    ax_cwnd.grid(alpha=0.3)
    ax_cwnd.legend(loc="best", fontsize=9)

    ax_tp.set_title(f"瞬時吞吐量 (滑動窗口 {throughput_window_ms:.0f}ms)")
    ax_tp.set_xlabel("時間 (ms)")
    ax_tp.set_ylabel("吞吐量 (Mbps)")
    ax_tp.grid(alpha=0.3)
    ax_tp.legend(loc="best", fontsize=9)

    ax_rtt.set_title("Smoothed RTT")
    ax_rtt.set_xlabel("時間 (ms)")
    ax_rtt.set_ylabel("RTT (ms)")
    ax_rtt.grid(alpha=0.3)
    ax_rtt.legend(loc="best", fontsize=9)

    ax_loss.set_title("累計丟包事件數")
    ax_loss.set_xlabel("時間 (ms)")
    ax_loss.set_ylabel("loss 事件數")
    ax_loss.grid(alpha=0.3)
    ax_loss.legend(loc="best", fontsize=9)

    fig.suptitle("QUIC 擁塞控制比較", fontsize=14, fontweight="bold")
    fig.savefig(output, dpi=140, bbox_inches="tight")
    print(f"\n✅ 已輸出對照圖: {output}")
    print("   (在 Mac 上可以直接執行: open " + output + ")")


# -------------------------------------------------------------------
# CLI
# -------------------------------------------------------------------

def main():
    p = argparse.ArgumentParser(
        description="從 aioquic qlog 畫四合一比較圖",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("--label", action="append", nargs="+",
                   metavar=("LABEL", "QLOG"),
                   help="指定一組 label + 一個或多個 qlog 檔。可用多次。"
                        " 例：--label \"Reno baseline\" logs/baseline_reno/*.qlog")
    p.add_argument("--auto", nargs="+", default=None,
                   help="自動模式：以每個 qlog 的父資料夾名當 label")
    p.add_argument("--output", "-o", default="compare.png",
                   help="輸出圖檔名 (預設 compare.png)")
    p.add_argument("--window", type=float, default=200,
                   help="吞吐量滑動窗口大小 ms (預設 200)")
    p.add_argument("qlog", nargs="*", help="(無 --label/--auto 時) 直接給的 qlog 檔")
    args = p.parse_args()

    groups: dict[str, list[str]] = defaultdict(list)

    if args.label:
        for spec in args.label:
            label = spec[0]
            for pattern in spec[1:]:
                files = sorted(glob.glob(pattern)) or [pattern]
                groups[label].extend(f for f in files if f.endswith(".qlog"))
    elif args.auto:
        for pattern in args.auto:
            for f in sorted(glob.glob(pattern)):
                if not f.endswith(".qlog"):
                    continue
                label = os.path.basename(os.path.dirname(f)) or "default"
                groups[label].append(f)
    elif args.qlog:
        for f in args.qlog:
            for path in sorted(glob.glob(f)) or [f]:
                if path.endswith(".qlog"):
                    groups[os.path.basename(path)].append(path)
    else:
        p.print_help()
        sys.exit(1)

    if not groups:
        print("❌ 沒有找到任何 qlog 檔", file=sys.stderr)
        sys.exit(1)

    print("📊 解析 qlog 中...")
    traces: list[Trace] = []
    for label, files in groups.items():
        print(f"\n  [{label}]")
        parsed = []
        for f in files:
            tr = parse_qlog(f, name=label)
            print(f"    {os.path.basename(f)}: "
                  f"cwnd 點數={len(tr.cwnd)}, "
                  f"sent={len(tr.t_sent)}, recv={len(tr.t_recv)}, "
                  f"loss={len(tr.t_loss)}, rtt 點數={len(tr.t_rtt)}")
            parsed.append(tr)
        merged = merge_traces(parsed, label)
        traces.append(merged)

    plot_compare(traces, output=args.output, throughput_window_ms=args.window)


if __name__ == "__main__":
    main()