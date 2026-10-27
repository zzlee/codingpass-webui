# CodingPass 開發交接文件 (HANDOFF.md)

本文件總結截至目前為止的開發進度、已驗證的功能、架構設計細節，以及後續交接工作指引。

---

## 1. 專案資訊 (Project Info)
- **專案名稱**：CodingPass (C/C++ 程式設計教學及自動閱卷系統)
- **當前階段**：**第一階段 (Phase 1) - 全面完成並驗證通過** 🎉
- **Multi-Agent 引擎**：
  - Orchestrator：Antigravity
  - Worker Agents：`pi coding agent --provider ttu-vllm --model qwen3.8-flash-nvfp4`

---

## 2. 第一階段完成成果 (Phase 1 Deliverables)

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
  - 預設監聽連接埠 `8010`（避免與本機 vLLM SSH Tunnel port 8000 衝突）。
  - `GET /api/health`：回傳系統健康狀態、gcc/g++ 版本、語言支援清單與限制。
  - `POST /api/run`：接收 `{ code, language, stdin }`，進行安全判題，回傳執行耗時、狀態碼、stdout、stderr 與編譯輸出。
  - 啟用 CORS Middleware。
- [x] **`backend/test_judge.py`** (自動化整合測試)：
  - 涵蓋 16 項嚴苛測試情境，**16/16 全部 PASS (FAILED: none)**。

### 2.3 學生端 WebUI (Frontend - React + Monaco + Tailwind)
- [x] **`frontend/src/monacoSetup.ts`**：
  - 本地打包 Monaco Editor（無需連線至外部 CDN，校園離線環境完全可用）。
  - 客製化 `codingpass-dark` 終端風格深色主題。
- [x] **`frontend/src/templates.ts`**：
  - 內建 C 與 C++ 共 6 款範本（Hello World、Stdin 兩數相加、Stdin 陣列排序與篩選），附帶標準輸入範例。
- [x] **`frontend/src/components/Header.tsx`**：
  - 品牌 Logo、C/C++ 切換分頁、範本快速下拉選單、執行按鈕 (含載入 Spinner 與 `Ctrl+Enter` 快捷鍵提示)、Judge 健康檢查指示燈 (連線/離線狀態展示與 Tooltip 工具鏈說明)。
- [x] **`frontend/src/components/ConsolePanel.tsx`**：
  - Standard Input (stdin) 輸入區：支援一鍵清空、字數統計。
  - Execution Result 輸出區：狀態徽章（SUCCESS 綠、COMPILE ERROR 紅、TIME LIMIT EXCEEDED 橘、RUNTIME ERROR 黃）、執行時間 / 編譯時間 / 結束代碼統計數據。
  - 雙分頁切換：標準輸出 (stdout) 與診斷訊息 (stderr / 語法錯誤回顯)，編譯失敗時自動切換至 Diagnostics。
  - 一鍵複製輸出結果功能。
- [x] **`frontend/src/App.tsx`**：
  - 58% : 42% 雙欄響應式分割佈局。
  - 雙語言獨立代碼緩衝區（切換 C / C++ 不丟失學生程式碼）。
  - 全局 Capture Phase 鍵盤快捷鍵（`Ctrl+Enter` / `Cmd+Enter`），在編輯器或 stdin 輸入框皆可一鍵觸發。
  - 定期 Health Polling 與斷線友善提示。
- [x] **Playwright 端到端 (E2E) 測試驗證**：
  - 真實 Chromium 瀏覽器驅動測試，**27/27 項測試全部通過**（包含 Editor 掛載、模板切換、stdin 執行、C++ 排序、編譯錯誤回顯、SIGSEGV 段錯誤、無窮迴圈 TLE 逾時中斷、自訂 stdin 快捷鍵等）。
  - TypeScript 型別檢查 `npx tsc -b`：**0 錯誤**。
  - 靜態語法檢查 `npx oxlint`：**0 警告**。
  - 正式環境建置 `npm run build`：**建置成功**。

---

## 3. 本地啟動指南 (Quick Start)

### 3.1 啟動後端裁判服務 (Backend)
```bash
cd backend
./venv/bin/uvicorn main:app --host 127.0.0.1 --port 8010 --reload
```

### 3.2 啟動前端開發伺服器 (Frontend)
```bash
cd frontend
npm run dev
# 瀏覽器訪問：http://localhost:5173
```

---

## 4. 下一步開發階段 (Phase 2 Roadmap)

第一階段（學生端寫代碼、編譯、stdin/stdout 本地執行）已完全就緒。下一步推進目標：
- **第二階段：多角色帳號與班級體系 (RBAC & Classes)**
  - 角色劃分：Admin、老師 (Teacher)、學生 (Student)。
  - 班級管理：老師創建班級、班級邀請碼、學生加入。
  - 認證與授權：JWT Authentication 與資料庫模型（SQLite / PostgreSQL）。
- **第三階段：題目庫與線上測驗 (Problem Bank & Exam Mode)**
  - 老師題目管理（題目 Markdown 敘述、記憶體與時間限制）。
  - 測資管理（公開範例測資、隱藏評測測資集）。
- **第四階段：AI 自動閱卷與評分建議 (AI Auto-Grading)**
  - 串接 `ttu-vllm qwen3.8-flash` 提供程式碼分析、時間/空間複雜度建議與風格改善。
- **第五階段：老師閱卷後台與成績總覽**
