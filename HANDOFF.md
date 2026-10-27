# CodingPass 開發交接文件 (HANDOFF.md)

本文件總結截至目前為止的開發進度、已驗證的功能、架構設計細節，以及後續交接工作指引。

---

## 1. 專案資訊 (Project Info)
- **專案名稱**：CodingPass (C/C++ 程式設計教學及自動閱卷系統)
- **當前階段**：第一階段 (Phase 1) - 學生端本地編譯與 stdin/stdout 執行環境
- **Multi-Agent 引擎**：
  - Orchestrator：Antigravity
  - Worker Agents：`pi coding agent --provider ttu-vllm --model qwen3.8-flash-nvfp4`

---

## 2. 目前已完成之項目 (Completed Milestones)

### 2.1 Multi-Agent 派工體系
- [x] **`scripts/agent_dispatch.py`**：可調派 `coder`、`reviewer`、`tester` 角色，自動注入角色系統提示詞，支援非互動與互動模式。
- [x] **`AGENTS.md`**：完整定義系統分工、調度指令、沙盒安全規範及歷史除錯經驗。

### 2.2 後端沙盒編譯與執行引擎 (Backend)
- [x] **`backend/compiler.py`** (高品質沙盒執行器)：
  - 支援 `c` (`gcc -O2 -Wall`) 與 `cpp` (`g++ -O2 -std=c++17 -Wall -lm`)。
  - 獨立臨時目錄（`/tmp/cj-*`）隔離編譯與執行。
  - 實作完整安全限制：
    - `RLIMIT_CPU` / Wall-clock 超時保護（預設 3 秒超時即 `SIGKILL` 整個 process group）。
    - `RLIMIT_AS` 記憶體上限防護（預設 256MB，超額捕捉 `std::bad_alloc` / `SIGSEGV`）。
    - `RLIMIT_CORE = 0` 與 `RLIMIT_FSIZE` 檔案大小限制。
    - 嚴格環境變數消毒（`_clean_env`，僅保留基礎 PATH、TMPDIR）。
    - 輸出流限制（`MAX_OUTPUT_BYTES` 256KB 防塞爆緩衝區）。
  - 精準度量執行時間（毫秒）與編譯時間。
- [x] **`backend/main.py`** (FastAPI 服務)：
  - `GET /api/health`：回傳系統健康狀態、gcc/g++ 版本、語言支援清單與限制。
  - `POST /api/run`：接收 `{ code, language, stdin }`，進行安全判題，回傳執行耗時、狀態碼、stdout、stderr 與編譯輸出。
  - 啟用 CORS Middleware，支援前端 `http://localhost:5173`。
- [x] **`backend/test_judge.py`** (自動化整合測試)：
  - 涵蓋 16 項嚴苛測試情境：C/C++ 正常執行與輸入、語法編譯錯誤回顯、Wall-clock 超時、CPU 密集超時、多行程 Process Group 終止清理、無殭屍進程殘留、段錯誤 (SIGSEGV) 回顯、記憶體超額 (OOM / bad_alloc)、非零退出碼、超大輸出截斷、8 組並行併發執行測試、臨時目錄自動清理。
  - **測試結果**：16 項測試全數 **PASS**（FAILED: none）。

### 2.3 前端專案環境 (Frontend Scaffolding)
- [x] 初始化 Vite + React 18 + TypeScript 專案。
- [x] 成功安裝必要相依套件：
  - `@monaco-editor/react` (VS Code 程式碼編輯器)
  - `tailwindcss` v4 + `@tailwindcss/vite` (UI 樣式框架)
  - `lucide-react` (現代化圖標集)

---

## 3. 下一步開發清單 (Immediate Next Steps for Phase 1)

1. **實作學生端 WebUI 介面** (`frontend/src/`):
   - **`App.tsx`**：主頁面排版（頂部工具列、左側 Monaco Editor、右側 stdin 輸入與 stdout 終端輸出面板）。
   - **語言選擇與預設範本**：提供標準 C 與 C++ 範本代碼（如 stdin 計算兩數之和）。
   - **執行狀態列**：動態展示編譯中、執行中、成功 (綠色)、編譯錯誤 (紅色)、超時 TLE (橘色) 及耗時統計。
   - **快捷鍵支援**：`Ctrl + Enter` / `Cmd + Enter` 一鍵編譯執行。
2. **端到端整合驗證**：
   - 啟動後端 (`uvicorn main:app --reload --port 8000`)。
   - 啟動前端 (`npm run dev -- --port 5173`)。
   - 在瀏覽器中實際進行 C/C++ 撰寫與 stdin 輸入輸出測試。

---

## 4. 後續規劃藍圖 (Roadmap)
- **Phase 2 (多角色與班級管理)**：使用者角色（Admin / Teacher / Student）、班級建立與成員加入、JWT 鑑權。
- **Phase 3 (題目庫與線上考試)**：老師 Markdown 出題、公開與隱藏測資管理、考試倒數計時與防切換視窗。
- **Phase 4 (AI 自動閱卷與評分建議)**：利用本地 `ttu-vllm qwen3.8-flash` 提供程式碼演算法分析、時空複雜度評語與風格建議。
- **Phase 5 (老師閱卷與成績儀表板)**：老師批閱介面、分數調整與報表導出。
