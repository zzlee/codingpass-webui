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
  - 回覆：`{"type": "result", "id": "req-1", "status": "success"|"compile_error"|"time_limit_exceeded"|"runtime_error", "stdout": "...", "stderr": "...", "compile_output": "...", "execution_time_ms": 1.2, "exit_code": 0}`
