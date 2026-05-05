"""
cwnd_monitor.py — aioquic 擁塞視窗即時監控（v2）

修正 v1 的問題：
  v1 只 patch 基礎類別 QuicCongestionControl，但 Reno/Cubic 都 override 了
  on_packet_acked / on_packets_lost，所以 patch 沒生效、csv 沒被寫入。
  v2 改成同時 patch 基礎類別與所有具體子類別 (Reno, Cubic, ...) 。

用法（在 server 或 client 程式最上面 import 並 install）：

    import cwnd_monitor
    cwnd_monitor.install("logs/cwnd_server.csv")
"""

from __future__ import annotations
import csv
import os
import time
from typing import Optional, TextIO

# 強制載入子類別模組（讓 __subclasses__() 找得到它們）
import aioquic.quic.congestion.reno   # noqa: F401
import aioquic.quic.congestion.cubic  # noqa: F401
from aioquic.quic.congestion.base import QuicCongestionControl

_log_file: Optional[TextIO] = None
_log_writer = None
_t0: Optional[float] = None
_installed = False


def _safe_attr(obj, name: str):
    v = getattr(obj, name, None)
    return "" if v is None else v


def _log(self, event: str) -> None:
    if _log_writer is None or _t0 is None:
        return
    t_ms = (time.time() - _t0) * 1000.0
    _log_writer.writerow([
        f"{t_ms:.2f}",
        event,
        self.congestion_window,
        _safe_attr(self, "ssthresh"),
        _safe_attr(self, "bytes_in_flight"),
        _safe_attr(self, "_rtt_smoothed"),
        type(self).__name__,
    ])
    _log_file.flush()


def _all_subclasses(cls):
    """遞迴抓出所有子類別。"""
    result = set()
    for sub in cls.__subclasses__():
        result.add(sub)
        result.update(_all_subclasses(sub))
    return result


def _make_wrapper(orig_method, event_name: str):
    def wrapper(self, *args, **kwargs):
        result = orig_method(self, *args, **kwargs)
        _log(self, event_name)
        return result
    return wrapper


def install(csv_path: str = "cwnd_log.csv") -> None:
    """安裝 monkey-patch。重複呼叫只會重置 csv，patch 仍只裝一次。"""
    global _log_file, _log_writer, _t0, _installed

    parent = os.path.dirname(os.path.abspath(csv_path))
    if parent:
        os.makedirs(parent, exist_ok=True)

    if _log_file is not None:
        _log_file.close()

    _log_file = open(csv_path, "w", newline="")
    _log_writer = csv.writer(_log_file)
    _log_writer.writerow([
        "t_ms", "event", "cwnd", "ssthresh", "bytes_in_flight",
        "rtt_smoothed", "algorithm",
    ])
    _t0 = time.time()

    if _installed:
        print(f"[cwnd_monitor] csv reset → {csv_path}")
        return

    # 收集所有要 patch 的類別：基礎類別 + 所有子類別
    classes_to_patch = [QuicCongestionControl] + list(_all_subclasses(QuicCongestionControl))

    patched_count = 0
    for cls in classes_to_patch:
        # 只 patch「該類別自己有定義」的方法（不要重複 patch 繼承來的）
        if "on_packet_acked" in cls.__dict__:
            cls.on_packet_acked = _make_wrapper(cls.on_packet_acked, "ack")
            patched_count += 1
        if "on_packets_lost" in cls.__dict__:
            cls.on_packets_lost = _make_wrapper(cls.on_packets_lost, "loss")
            patched_count += 1

    _installed = True
    class_names = [c.__name__ for c in classes_to_patch]
    print(f"[cwnd_monitor] installed → {csv_path}")
    print(f"[cwnd_monitor] patched {patched_count} method(s) on classes: {class_names}")


def close() -> None:
    global _log_file, _log_writer
    if _log_file is not None:
        _log_file.close()
        _log_file = None
        _log_writer = None