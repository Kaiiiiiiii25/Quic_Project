"""
analyze_csv.py — 從 cwnd_monitor 的 csv 直接畫圖

如果你的 qlog 太多/格式有問題，可以直接用 cwnd_server.csv 來做視覺化。
這個比 qlog 版本少了「吞吐量」和「RTT」（因為 cwnd csv 沒記這些），
但 cwnd 和 loss 都有，已經夠秀了。

用法：
  python analyze_csv.py \\
      --label "Reno baseline"   logs/cwnd_server_baseline_reno.csv \\
      --label "Cubic baseline"  logs/cwnd_server_baseline_cubic.csv \\
      --label "Reno + 10% loss" logs/cwnd_server_loss10_reno.csv \\
      --label "Cubic + 10% loss" logs/cwnd_server_loss10_cubic.csv \\
      --output compare.png
"""

from __future__ import annotations
import argparse
import csv
import os
import sys
from dataclasses import dataclass, field

import matplotlib.pyplot as plt


plt.rcParams["font.sans-serif"] = [
    "PingFang TC", "PingFang SC", "Heiti TC", "Arial Unicode MS",
    "Noto Sans CJK TC", "DejaVu Sans"
]
plt.rcParams["axes.unicode_minus"] = False


@dataclass
class CsvTrace:
    label: str
    t_ms: list[float] = field(default_factory=list)
    cwnd: list[float] = field(default_factory=list)
    biff: list[float] = field(default_factory=list)
    t_loss: list[float] = field(default_factory=list)
    cwnd_at_loss: list[float] = field(default_factory=list)


def load_csv(path: str, label: str) -> CsvTrace:
    tr = CsvTrace(label=label)
    with open(path, newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                t = float(row["t_ms"])
                cw = float(row["cwnd"])
                bf = float(row["bytes_in_flight"]) if row.get("bytes_in_flight") else 0
            except (ValueError, KeyError):
                continue
            tr.t_ms.append(t)
            tr.cwnd.append(cw)
            tr.biff.append(bf)
            if row.get("event") == "loss":
                tr.t_loss.append(t)
                tr.cwnd_at_loss.append(cw)
    # 把時間歸零
    if tr.t_ms:
        t0 = tr.t_ms[0]
        tr.t_ms = [t - t0 for t in tr.t_ms]
        tr.t_loss = [t - t0 for t in tr.t_loss]
    return tr


COLOR_MAP = {
    "reno baseline":     "#185FA5",
    "cubic baseline":    "#1D9E75",
    "reno + 10% loss":   "#993C1D",
    "cubic + 10% loss":  "#854F0B",
}
FALLBACK = ["#185FA5", "#1D9E75", "#993C1D", "#854F0B",
            "#534AB7", "#A32D2D", "#3B6D11", "#993556"]


def color_of(label: str, idx: int) -> str:
    return COLOR_MAP.get(label.lower().strip(), FALLBACK[idx % len(FALLBACK)])


def plot(traces: list[CsvTrace], output: str) -> None:
    fig, axes = plt.subplots(2, 1, figsize=(13, 8), constrained_layout=True,
                              sharex=False)
    ax_cwnd, ax_loss = axes

    for i, tr in enumerate(traces):
        c = color_of(tr.label, i)

        # cwnd 主線
        cwnd_kb = [v / 1024 for v in tr.cwnd]
        ax_cwnd.plot(tr.t_ms, cwnd_kb, color=c, label=tr.label,
                     linewidth=1.3, alpha=0.9)

        # loss 點 — 在 cwnd 上點紅
        if tr.t_loss:
            loss_kb = [v / 1024 for v in tr.cwnd_at_loss]
            ax_cwnd.scatter(tr.t_loss, loss_kb, color=c, s=18,
                            marker="x", linewidths=1.2, zorder=5)

        # 累計 loss 數
        if tr.t_loss:
            cum = list(range(1, len(tr.t_loss) + 1))
            ax_loss.step(tr.t_loss, cum, color=c, label=tr.label,
                         linewidth=1.4, where="post")
        else:
            ax_loss.plot([], [], color=c, label=f"{tr.label} (0 loss)")

    ax_cwnd.set_title("Congestion Window 隨時間變化（× 為 loss 事件）",
                      fontsize=12, fontweight="bold")
    ax_cwnd.set_xlabel("時間 (ms)")
    ax_cwnd.set_ylabel("cwnd (KB)")
    ax_cwnd.grid(alpha=0.3)
    ax_cwnd.legend(loc="best", fontsize=9)

    ax_loss.set_title("累計丟包事件數", fontsize=12, fontweight="bold")
    ax_loss.set_xlabel("時間 (ms)")
    ax_loss.set_ylabel("累計 loss 事件")
    ax_loss.grid(alpha=0.3)
    ax_loss.legend(loc="best", fontsize=9)

    fig.suptitle("QUIC 擁塞控制比較（cwnd_monitor csv 來源）",
                 fontsize=14, fontweight="bold")
    fig.savefig(output, dpi=140, bbox_inches="tight")
    print(f"\n✅ 已輸出: {output}")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--label", action="append", nargs=2,
                   metavar=("LABEL", "CSV"),
                   help="指定一組 label + 一個 csv 檔。可用多次。", required=True)
    p.add_argument("--output", "-o", default="compare_csv.png")
    args = p.parse_args()

    print("📊 讀取 csv 中...")
    traces = []
    for label, path in args.label:
        if not os.path.exists(path):
            print(f"  ⚠️  {path} 不存在，跳過")
            continue
        tr = load_csv(path, label)
        print(f"  [{label}] {os.path.basename(path)}: "
              f"{len(tr.t_ms)} 個事件, {len(tr.t_loss)} 個 loss, "
              f"cwnd 範圍 {min(tr.cwnd)/1024:.1f}~{max(tr.cwnd)/1024:.1f} KB")
        traces.append(tr)

    if not traces:
        print("❌ 沒讀到任何 csv", file=sys.stderr)
        sys.exit(1)

    plot(traces, args.output)


if __name__ == "__main__":
    main()
