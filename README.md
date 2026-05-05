# 第三階段操作手冊：數據可視化

> 環境：`~/Desktop/Quic_Project/`，venv 叫 `quic`
> 目標：把第二階段產生的 qlog/csv 變成「秀給教授看」的圖

兩條線並進：

1. **線上工具 qvis** — 五分鐘搞定，免寫程式
2. **自己的 Python 腳本** — 客製化、可疊加多組對照、可放進報告

---

## 步驟 0：放入新檔案

把這兩個檔案下載放到 `~/Desktop/Quic_Project/`：

- `analyze_qlog.py`（從 qlog 畫圖，4 子圖：cwnd / 吞吐量 / RTT / loss）
- `analyze_csv.py`（直接吃 cwnd_monitor csv，2 子圖：cwnd / loss）

安裝 matplotlib（之前 venv 還沒裝）：

```bash
cd ~/Desktop/Quic_Project
source quic/bin/activate
pip install matplotlib
```

---

## 路線 A：qvis 線上視覺化（最快）

> 直接把 qlog 拖進去，免寫程式碼。但每次只能看一個 trace，沒辦法做疊加比較。

1. 開瀏覽器去 <https://qvis.quictools.info/#/files>
2. 把 `~/Desktop/Quic_Project/logs/baseline_reno/` 裡任一個 `.qlog` 拖到頁面上
3. 上方 tab 切到 **Congestion** → 看 cwnd / bytes_in_flight / sent rate 三條線疊在一起
4. 切到 **Sequence** → 看每個封包的時間軸（loss 會用紅色標）
5. 截圖。對 4 個條件（baseline_reno、baseline_cubic、loss10_reno、loss10_cubic）各做一次

⚠️ **截圖前先確認左上 trace 名稱對得上條件**，避免報告裡標錯。

---

## 路線 B：自己的 Python 腳本（重點）

### B-1. 用 cwnd csv 畫（先驗證流程，最快）

如果你已經有保留 4 個 server csv：

```bash
python analyze_csv.py \
    --label "Reno baseline"      logs/cwnd_server_baseline_reno.csv \
    --label "Cubic baseline"     logs/cwnd_server_baseline_cubic.csv \
    --label "Reno + 10% loss"    logs/cwnd_server_loss10_reno.csv \
    --label "Cubic + 10% loss"   logs/cwnd_server_loss10_cubic.csv \
    --output cwnd_compare.png

open cwnd_compare.png
```

如果你只有當下這次的 `cwnd_server.csv`（前面忘了 mv），就先單張畫看看：

```bash
python analyze_csv.py \
    --label "Cubic + 10% loss" logs/cwnd_server.csv \
    --output single.png

open single.png
```

預期看到：上半 cwnd 三次起飛、× 標出 loss 點；下半累計 loss 數呈階梯狀。

### B-2. 用 qlog 畫（最完整，4 子圖）

qlog 比 csv 多了 `RTT` 和 `throughput` 資訊，腳本會畫成 2×2 四子圖：

```bash
# 完整四組對照
python analyze_qlog.py \
    --label "Reno baseline"      logs/baseline_reno/*.qlog \
    --label "Cubic baseline"     logs/baseline_cubic/*.qlog \
    --label "Reno + 10% loss"    logs/loss10_reno/*.qlog \
    --label "Cubic + 10% loss"   logs/loss10_cubic/*.qlog \
    --output qlog_compare.png

open qlog_compare.png
```

每個 label 後面可以放多個 qlog（一組 `--runs 3` 會產三個 qlog），腳本會自動挑「資料量最大」那個當代表。

#### 進階：自動模式

如果你的目錄結構乾淨（每個條件一個資料夾），可以一行解決：

```bash
python analyze_qlog.py --auto logs/*/*.qlog --output auto.png
```

自動以資料夾名當 label。

#### 調整滑動窗口

吞吐量是用滑動窗口算瞬時值，預設 200ms。資料密的話可以縮短：

```bash
python analyze_qlog.py --window 100 --label "Reno" logs/baseline_reno/*.qlog ...
```

---

## 步驟解讀：怎麼跟教授講這張圖

四個子圖的故事線：

| 子圖 | 你要講的話 |
|---|---|
| **cwnd 隨時間** | 「Reno 的鋸齒比 Cubic 銳利 — 因為 Reno 是 multiplicative decrease 砍半，Cubic 用立方函數比較平滑」 |
| **吞吐量** | 「baseline 兩條線都接近頻寬上限，10% loss 條件下 throughput 掉到 1/10 以下」 |
| **RTT** | 「網路模擬加 50ms 後 RTT 明顯抬升，符合預期」 |
| **累計 loss** | 「loss10 條件下總 loss 數隨時間線性增加（穩定 10% 機率），baseline 接近零」 |

把這張圖搭配 throughput_test.py 印的吞吐量數字（做成 4×1 表格），第三階段的成果就完整了。

---

## 沒有 qlog 怎麼辦？

第一階段的指令已經有 `-q logs/`，所以**任何一次 client 跑完 logs 裡都會有新的 qlog**。再跑一次就是了：

```bash
# Terminal 1: server
python http3_server.py -c cert.pem -k key.pem \
    --host localhost --port 4433 -q logs/baseline_reno \
    --congestion-control-algorithm reno demo:app

# Terminal 2:
python throughput_test.py --ca-certs cert.pem \
    --algo reno --size 10MB --runs 3 \
    --qlog-dir logs/baseline_reno
```

注意 server 的 `-q` 跟 client 的 `--qlog-dir` 各自分開存就好（server 端 qlog 也是有效的）。

---

## 常見問題

**Q: 圖出現方框/亂碼**
A: 中文字型沒找到。執行：
```bash
python -c "import matplotlib.font_manager as fm; print([f.name for f in fm.fontManager.ttflist if 'PingFang' in f.name or 'Heiti' in f.name])"
```
應該至少看到一個。如果是空的，編輯 `analyze_qlog.py` 把 `font.sans-serif` 那個 list 第一項改成你系統有的字型名稱。

**Q: `analyze_qlog.py` 報「沒有找到任何 qlog 檔」**
A: 檢查路徑，`logs/baseline_reno/*.qlog` 用 ls 確認真的有檔案。注意 zsh 的 glob 會展開但 argparse 拿到的是展開後的 list，所以指令照貼即可。

**Q: 跑出來圖是空的（線都沒畫）**
A: qlog 解析失敗。先單獨跑一個檔案看 debug 訊息：
```bash
python analyze_qlog.py logs/baseline_reno/abc.qlog
```
如果 `cwnd 點數=0, sent=0` → 你的 aioquic 版本 qlog schema 不一樣，把任一個 qlog 上傳給我，我加新的格式 parser。

**Q: throughput 子圖看起來很跳**
A: 把 `--window` 調大（試 500 或 1000），會比較平滑。

---

## 第三階段交付清單

- [ ] qvis 截圖 4 張（4 個條件各一張 Congestion graph）
- [ ] `analyze_qlog.py` 跑出的 4 條件對照圖一張
- [ ] 一張吞吐量數字表（4×1）
- [ ] 一兩段文字解讀（用上面的「怎麼跟教授講」）

集滿就可以進第四階段（自製演算法 / GUI 工具）。
