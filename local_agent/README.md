# CodingPass Local Agent (學生端本地編譯執行代理)

## 📖 簡介
**CodingPass Local Agent** 是一套專為課堂環境設計的輕量級本地端編譯執行代理服務。

在 40 人以上的程式設計大班課程中，若所有學生每次在網頁上點擊「執行程式 (Run Code)」或「測試範例 (Run Samples)」都交由伺服器編譯 C/C++，將瞬間耗盡伺服器 CPU 運算資源並導致請求排隊等待。

透過在學生電腦啟動本 Local Agent：
1. **網頁與本地連動**：瀏覽器透過 `ws://127.0.0.1:8765` 與本地代理保持連線。
2. **本地編譯加速**：日常除錯、範例測資測試直接在學生電腦完成編譯與執行，速度即時且伺服器負載為零。
3. **正式評測公正性**：正式提交審查 (Submit) 依然送回伺服器端沙盒比對隱藏測資，防範作弊與測資外洩。
4. **無痛降級 (Fallback)**：若學生電腦未啟動 Agent，系統將自動無縫切換回雲端伺服器編譯執行。

---

## 🚀 啟動方式

### Windows 學生端
1. 解壓縮發放的 `codingpass-agent-win.zip`。
2. 雙擊執行 `run_agent.bat`。
3. 看到 `Listening on ws://127.0.0.1:8765` 即表示已連線就緒。
4. 打開 CodingPass 網頁，頂部導航列將顯示 `🟢 本地編譯加速 (Local Agent)`。

### Linux / macOS 學生端
```bash
./run_agent.sh
# 或
python3 agent.py
```

---

## 🛠️ 編譯器要求
Local Agent 需在本地端調用 C/C++ 編譯器：
- **Windows**: 支援系統 `MinGW-w64` / `gcc`，或直接將 `w64devkit` 解壓於 `local_agent/w64devkit`。
- **Linux**: 需安裝 `build-essential`（`gcc` / `g++`）。
- **macOS**: 需安裝 Xcode Command Line Tools（`xcode-select --install`）。

---

## 📡 通訊協議 (WebSocket RFC 6455)
- **連線位址**：`ws://127.0.0.1:8765`
- **Ping 探測**：
  - 送出：`{"type": "ping"}`
  - 回覆：`{"type": "pong", "version": "1.0.0", "compiler": "g++ 13.2.0"}`
- **執行請求**：
  - 送出：`{"type": "run", "id": "req-1", "code": "...", "language": "cpp", "stdin": "...", "timeout_sec": 3.0}`
  - 回覆：`{"type": "result", "id": "req-1", "status": "success"|"compile_error"|"time_limit_exceeded"|"runtime_error", "stdout": "...", "stderr": "...", "compile_output": "...", "compile_time_ms": 132.4, "execution_time_ms": 1.2, "exit_code": 0, "stdout_truncated": false}`
- **批次執行請求（編譯一次、執行多組測資）**：
  - 送出：`{"type": "run_batch", "id": "req-2", "code": "...", "language": "cpp", "stdin_list": ["1 2\n", "3 4\n"], "timeout_sec": 3.0}`
  - 回覆：`{"type": "batch_result", "id": "req-2", "status": "ok"|"compile_error", "compile_time_ms": 132.4, "compile_output": "...", "cases_executed": 2, "cases_skipped": 0, "results": [{"index": 0, "status": "success", "stdout": "3", ...}, ...]}`
  - 單次批次最多執行 `MAX_BATCH_CASES`（預設 `64`，可用 `CODINGPASS_MAX_BATCH_CASES` 調整）組測資，超出部分記入 `cases_skipped`。預設值刻意 **不小於** 伺服器端的 `MAX_TEST_CASES = 50`，否則本地判題會 silently 丟掉尾端的測資。

---

## 🔒 安全加固 (Security Hardening)

1. **Cross-Site WebSocket Hijacking (CSWSH) 防護**：
   - `handshake()` 會檢查 `Origin` 標頭，僅允許主機為 `localhost` / `127.0.0.1` / `[::1]`，或是 **確實屬於私有位址的 IP 字面值**（`10/8`、`172.16/12`、`192.168/16`、`fc00::/7`、`fe80::/10`，由 `ipaddress` 判定）；其餘來源一律回覆 `HTTP/1.1 403 Forbidden` 並關閉連線。
   - 注意是「私有 IP 字面值」而 **不是** `172.` 之類的字串前綴：`172/8` 中只有 `172.16/12` 是內網，`172.104.x.x`（機房）、`172.217.x.x`（Google）都是可公開路由的位址；若以字串前綴放行，掛在這些公開 IP 上的網頁就能通過檢查，讓 Agent 在學生電腦上編譯並執行任意程式碼。同樣地，`10.attacker.example.com` 這類網域也不會被誤信（不是 IP 就直接拒絕）。
   - 非瀏覽器客戶端（CLI、自動化測試）不帶 `Origin` 標頭時照常放行 —— 這代表 **驗證只對瀏覽器生效**，所以請務必讓 Agent 只綁定 loopback；若 `CODINGPASS_AGENT_HOST` 不是 loopback，啟動時會記錄明確的警告。
   - 課堂伺服器使用其他網域時，可以逗號分隔白名單放行：
     ```bash
     CODINGPASS_ALLOWED_ORIGINS="http://classroom.local:8000,http://10.1.2.3" python3 agent.py
     ```
2. **輸出上限**：`stdout` / `stderr` 各自最多保留 `MAX_OUTPUT_BYTES = 256KB`，其餘資料仍會被完整讀取（避免 pipe 死鎖）但不進記憶體，並以 `stdout_truncated` / `stderr_truncated` 旗標與截斷告示回報。
3. **進程樹清理**：編譯與執行均以 `start_new_session=True`（POSIX）/ `CREATE_NEW_PROCESS_GROUP`（Windows）啟動独立進程群組；逾時時在 POSIX 以 `os.killpg(..., SIGKILL)`、在 Windows 以 `taskkill /F /T /PID`（並且確實檢查回傳碼，失敗才退回 `proc.kill()`）摧毀整棵進程樹，防止 `fork()` 迴圈或子進程殘留。單例執行逾時上限被夾到 `MAX_TIMEOUT_SEC = 30s`。
4. **管線有 deadline（不會被孤兒進程卡死）**：三個 pipe 的讀寫執行緒共用一個可縮短的 deadline；子進程結束後只再給 `PIPE_CLOSE_GRACE_SEC`。每條 pipe 只由其讀取執行緒關閉 —— 否則學生程式只要 `fork()` 後讓父進程退出、孤兒繼續握著 stdout，主執行緒的 `close()` 就會永遠等那個在飛中的 read（連帶卡死整個 Agent）。若偵測到這種孤兒，會改用「指名 SIGKILL 進程群組成員」的方式清理（程序已 reap 後 `killpg(pgid)` 可能打到已被回收的 pgid，故不直接使用）。
5. **工作目錄隔離**：編譯與每次執行都在該次請求專用的暫存目錄（批次執行時每組測資另有 `run-<i>` 子目錄，跑完即刪）中進行，學生程式寫出的檔案不會落在啟動 `agent.py` 的那個目錄（通常是學生的家目錄/專案目錄）。
6. **編譯產物唯讀**：編譯出的 binaries 會被 `chmod 0o500`，避免同一批次後面的測資執行到被前面測資覆寫過的 binary。
7. **編譯器資訊不再阻塞 event loop**：`get_compiler_info()` 結果會快取，且 `ping` 處理改走 executor，避免單一客戶端連續 ping 讓所有連線一起卡住最多 2 秒。
8. **WebSocket 框架上限**：單一文字框架最大 `MAX_FRAME_BYTES`（預設 8 MiB，可用 `CODINGPASS_MAX_FRAME_BYTES` 調整）；宣告超大的框架長度直接斷線，避免攻擊者用 64-bit 長度欄位撐爆記憶體。Ping 以迴圈回應（不再遞迴），大量 ping 框架不會把呼叫堆疊壓爆破 `RecursionError`。

> ⚠️ **本地端沒有 `rlimit` 隔離**：Agent 只限制 wall-clock 時間與輸出位元組數，沒有 `RLIMIT_AS` / `RLIMIT_CPU` / `RLIMIT_NPROC` / `RLIMIT_FSIZE`（那是伺服器端沙盒 `backend/compiler.py` 的層級）。在 Windows 上以 `preexec_fn` 設限不可行，在 POSIX 上則與多執行緒 `fork` 有死鎖風險，因此刻意留白：本地端執行的是學生自己的程式、在學生自己的電腦上。若要在不可信任的網路上下發程式碼，請先補上這層（或改用容器 / `nsjail`）。

