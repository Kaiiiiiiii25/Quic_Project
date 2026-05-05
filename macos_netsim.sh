#!/bin/bash
# macos_netsim.sh — macOS 上的「Clumsy / tc qdisc」對應方案
#
# 使用內建的 dummynet (dnctl) + pfctl，對 UDP port 4433 加上：
#   - 隨機封包遺失
#   - 固定延遲
#   - 頻寬限制
#
# 比 Network Link Conditioner 好的地方：可以只針對特定 port，不影響其他連線。
#
# === 用法 ===
#   sudo ./macos_netsim.sh on   --loss 10 --delay 100 --bw 10Mbit
#   sudo ./macos_netsim.sh on   --loss 5  --delay 50
#   sudo ./macos_netsim.sh off
#   sudo ./macos_netsim.sh status
#
# 注意：必須用 sudo。預設只攔截 UDP port 4433 (你 server 的 port)。

set -e

PORT=4433
PIPE=1   # dnctl 的 pipe 編號
ANCHOR="quic_sim"

usage() {
    cat <<EOF
用法: sudo $0 <on|off|status> [選項]

  on  選項：
    --loss   <pct>          封包遺失率 % (例 10)
    --delay  <ms>           單向延遲 ms (例 100)
    --bw     <rate>         頻寬限制 (例 10Mbit / 1Mbit / 500Kbit)
    --port   <udp_port>     要影響的 UDP port (預設 $PORT)
EOF
    exit 1
}

CMD="${1:-}"
shift || true

LOSS=""
DELAY=""
BW=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --loss)  LOSS="$2"; shift 2 ;;
        --delay) DELAY="$2"; shift 2 ;;
        --bw)    BW="$2"; shift 2 ;;
        --port)  PORT="$2"; shift 2 ;;
        *) echo "未知選項: $1"; usage ;;
    esac
done

if [[ "$EUID" -ne 0 ]]; then
    echo "❌ 需要 sudo 執行"; exit 1
fi

case "$CMD" in
    on)
        echo "🚦 開啟網路模擬 (UDP port $PORT)"

        # 1. 設定 dummynet pipe 的特性
        CONFIG="dnctl pipe $PIPE config"
        [[ -n "$BW"    ]] && CONFIG="$CONFIG bw $BW"
        [[ -n "$DELAY" ]] && CONFIG="$CONFIG delay $DELAY"
        [[ -n "$LOSS"  ]] && CONFIG="$CONFIG plr 0.$(printf '%02d' $LOSS)"
        # ↑ plr 接受 0~1，0.10 = 10%
        echo "  $CONFIG"
        eval "$CONFIG"

        # 2. 用 pfctl 把 UDP port 4433 的雙向流量導入這個 pipe
        cat > /tmp/${ANCHOR}.conf <<EOF
dummynet in  proto udp from any to any port $PORT pipe $PIPE
dummynet out proto udp from any to any port $PORT pipe $PIPE
dummynet in  proto udp from any port $PORT to any pipe $PIPE
dummynet out proto udp from any port $PORT to any pipe $PIPE
EOF

        pfctl -a "$ANCHOR" -f /tmp/${ANCHOR}.conf 2>/dev/null
        pfctl -E 2>&1 | grep -i token || true

        echo "✅ 已套用：loss=${LOSS:-0}%  delay=${DELAY:-0}ms  bw=${BW:-unlimited}"
        echo "   做完實驗後請記得跑：sudo $0 off"
        ;;

    off)
        echo "🧹 關閉網路模擬"
        pfctl -a "$ANCHOR" -F all 2>/dev/null || true
        dnctl -q flush 2>/dev/null || true
        rm -f /tmp/${ANCHOR}.conf
        echo "✅ 已清除"
        ;;

    status)
        echo "=== dnctl pipe ==="
        dnctl list 2>/dev/null || echo "(無)"
        echo ""
        echo "=== pf anchor: $ANCHOR ==="
        pfctl -a "$ANCHOR" -s rules 2>/dev/null || echo "(無)"
        ;;

    *)
        usage
        ;;
esac
