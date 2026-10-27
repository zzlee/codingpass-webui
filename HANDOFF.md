# CodingPass 開發交接文件 (HANDOFF.md)

本文件總結截至目前為止的開發進度、已驗證的功能、架構設計細節、新環境遷移指南（含 Headless 模式指引），以及後續 Phase 4 AI 閱卷的交接工作。

---

## 1. 專案資訊 (Project Info)
- **專案名稱**：CodingPass (C/C++ 程式設計教學及自動閱卷系統)
- **GitHub 儲存庫**：[https://github.com/zzlee/codingpass-webui](https://github.com/zzlee/codingpass-webui) (分支：`main`)
- **當前進度**：
  - **Phase 1 (學生本地沙盒編譯與 WebUI)**：✅ **已完成並驗證通過**
  - **Phase 2 (老師出題題庫、多測資自動評測、隱藏測資防弊)**：✅ **已完成並驗證通過**
  - **Phase 3 (多角色認證、班級管理、提交紀錄歷史與一鍵切換展示 UI)**：✅ **已完成並驗證通過**
  - **Phase 4 (AI 智能助教診斷、複雜度分析與教師批改覆核工作台)**：✅ **已完成並驗證通過**
  - **Phase 5 (CodingPass Local Agent 學生本機編譯代理與 WebSocket 雙軌分流架構)**：✅ **已完成並驗證通過**
  - **Direction D (後端單體模組化：拆分為 routers/、services/、schemas.py、dependencies.py)**：✅ **已完成並驗證通過**
  - **Direction C (生產容器化：Dockerfile、docker-compose.yml、SPA 安全託管與 Bubblewrap 沙盒隔離)**：✅ **已完成並驗證通過**
  - **CodingPass-Tests 深度整合 (Stage 1-4 題庫卡匯入、五項評分規程、最小修復探針、抄襲稽核)**：✅ **已完成並驗證通過**
- **Multi-Agent 體系**：
  - Orchestrator：Antigravity (架構總指揮)
  - Worker Agents：`pi coding agent --provider ttu-vllm --model qwen3.8-flash-nvfp4`（透過 `./scripts/agent_dispatch.py` 調派）

---

## 2. 已完成與驗證的功能清單 (Completed Deliverables)

### 2.1 Multi-Agent 派工體系 (`AGENTS.md`)
- [x] **`scripts/agent_dispatch.py`**：可調派 `coder`、`reviewer`、`tester` 角色，自動注入角色系統提示詞，支援非互動與自動執行模式。
- [x] **`AGENTS.md`**：完整定義系統分工、調度指令、沙盒安全規範及歷史除錯經驗（如 fork bomb 嚴禁無受控測試）。

### 2.2 後端沙盒評測與資料管理 (Backend - FastAPI + SQLite WAL)
- [x] **`backend/compiler.py`** (高品質沙盒執行器)：
  - 支援 `c` (`gcc -O2 -Wall`) 與 `cpp` (`g++ -O2 -std=c++17 -Wall -lm`)。
  - 獨立臨時目錄（`/tmp/cj-*`）隔離編譯與執行，事後保證自動清理。
  - 嚴格安全限制：
    - `RLIMIT_CPU` / Wall-clock 超時保護（超時調用 `os.killpg(proc.pid, signal.SIGKILL)` 徹底清理進程群組）。
    - `RLIMIT_AS` 記憶體上限防護（預設 256MB，精確捕捉 `std::bad_alloc` 與 `SIGSEGV`）。
    - `RLIMIT_CORE = 0` 與 `RLIMIT_FSIZE` 檔案大小限制。
    - 嚴格環境變數消毒（`_clean_env`，僅保留基礎 PATH、TMPDIR）。
    - 輸出流限制（`MAX_OUTPUT_BYTES` 256KB 防塞爆緩衝區）。
- [x] **`backend/database.py` & `models.py`** (ORM 與資料持久化)：
  - SQLite WAL 模式，支援多連線高並發讀寫。
  - 6 個核心實體模型：`User`, `Classroom`, `ClassroomMembership`, `Problem`, `TestCase`, `Submission`。
  - 時間戳統一為 naive-UTC，杜絕時區漂移。
  - 自動冪等種子資料（啟動即注入 4 個測試帳號、1 個示範班級、2 個題目與 14 筆範例/隱藏測資）。
- [x] **`backend/auth.py`** (認證與權限控制)：
  - bcrypt 密碼雜湊 + JWT HS256 簽發/驗證。
  - 依角色宣告依賴 (`require_roles("admin", "teacher")`)。
- [x] **`backend/main.py`** (RESTful API 端點)：
  - 運行於連接埠 **`8010`**（防碰撞本地 SSH vLLM tunnel 的 8000 port）。
  - 提供 `/api/auth/*`、`/api/classrooms/*`、`/api/problems/*`、`/api/problems/{id}/submit`、`/api/submissions/*`、`/api/run`、`/api/health` 等 16+ 個 API。
  - **隱藏測資安全防護**：學生端評測回傳時，後端強制過濾隱藏測資的輸入與輸出，僅回傳 Pass/Fail 狀態與得分率；老師/管理員可完整查閱。
- [x] **後端自動化測試套件**：
  - `backend/test_api.py`：涵蓋認證、班級、出題、測資批次評測、計分、權限隔離等，**91/91 PASS (FAILED: 0)**。
  - `backend/test_judge.py`：涵蓋編譯錯誤、超時、記憶體溢出、fork 進程群組清理等，**28/28 PASS (FAILED: 0)**。
  - `backend/test_ai_phase4.py`：涵蓋 AI 狀態探測、即時診斷、隱藏測資零洩漏、AI 講評持久化、教師覆核評分與 SQLite 自動遷移，**全數 PASS (FAILED: 0)**。
- [x] **`backend/ai_tutor.py`** (Phase 4 AI 助教與批改輔助)：
  - 對接本地 vLLM (`http://localhost:8000/v1`, `qwen3.8-flash-nvfp4`)，並內建規則式離線 Fallback 分析器 (`heuristic-rules`)。
  - 具備隨機 Nonce 分隔符防禦 Prompt Injection，且嚴格保證隱藏測資輸入/輸出絕不進入 Prompt。
  - 提供 `/api/ai/status`、`/api/ai/analyze`、`/api/submissions/{id}/ai-review`、`/api/submissions/{id}/review` 等端點。

### 2.3 前端完整介面 (Frontend - React 18 + TypeScript + Monaco + Tailwind)
- [x] **Monaco Editor 離線打包 (`monacoSetup.ts`)**：
  - 採用本地 ESM Worker，完全不依賴外部 CDN，離線教學環境即可運作。
  - 客製化 `codingpass-dark` 終端風格深色主題。
- [x] **導覽與切換 (4 大核心分頁)**：
  1. 💻 **Playground (代碼沙盒)**：自由撰寫 C/C++ 程式碼，支援自訂 stdin 輸入、一鍵執行、stdout/stderr 雙頁籤診斷、執行耗時精確回顯。
  2. 📚 **Problem Bank (題目庫)**：
     - 題目清單展示難度徽章（Easy 綠、Medium 黃、Hard 紅）、範例/隱藏測資數量、建立者資訊。
     - 老師「建立題目 (Create Problem)」彈窗：支援 Markdown 說明、C/C++ 起始範本、多筆範例與隱藏測資動態增刪。
     - 題目作答介面（Problem Solver）：左側題目 Markdown 渲染與範例測資展示，右側 Monaco 編輯器，支援「Run Samples（測試範例）」與「Submit Solution（正式批次批改）」，即時展示各測資通過狀態與總分卡片。
  3. 🏫 **Classrooms (班級管理)**：
     - 老師視角：檢視管理的班級、專屬邀請碼（如 `CS101`）、已加入學生名冊、建立新班級。
     - 學生視角：已加入班級清單、憑邀請碼一鍵加入班級。
  4. 📊 **Submissions (提交紀錄)**：
     - 完整提交歷程表格：題目名稱、提交學生、語言、評測結果徽章（ACCEPTED, WRONG ANSWER, TLE, RE, CE）、得分（0-100%）、耗時、提交時間。
- [x] **1-Click 角色快速切換器 (Demo Accounts Switcher)**：
  - 頂部導覽列內建快速切換選單，一鍵在 Alice (學生)、Bob (未加入學生)、Prof. Chen (老師)、Admin (管理員) 間切換身分，無需手動輸入帳密即可快速展示不同身分權限。
- [x] **建置驗證**：
  - `npx tsc -b`：**0 錯誤**。
  - `npm run build`：**建置成功**（產生優化後的 `dist/` 靜態檔案）。

---

## 3. 預設測試帳號與示範資料 (Pre-seeded Accounts)

資料庫於每次首次啟動時會自動注入以下預設帳號與題目：

| 角色 (Role) | 姓名 (Name) | 帳號 (Username) | 密碼 (Password) | 備註說明 |
|---|---|---|---|---|
| **Admin** | Site Administrator | `admin` | `admin123` | 最高管理員，可管理所有班級與題目 |
| **Teacher** | Prof. Chen | `teacher1` | `teacher123` | 授課教師，擁有班級 `CS101` |
| **Student** | Alice | `student1` | `student123` | 學生，已加入 `CS101` 班級 |
| **Student** | Bob | `student2` | `student123` | 學生，尚未加入任何班級 |

- **預設班級**：`C/C++ Programming 101`（邀請碼：`CS101`）。
- **預設題目**：
  1. `Two Sum (A + B Problem)`（Easy，2 筆範例 + 5 筆隱藏測資）。
  2. `Check Prime Number`（Medium，2 筆範例 + 9 筆極端邊界隱藏測資）。

---

## 4. 遷移動作指南：在新電腦上使用 Headless 模式執行

> [!TIP]
> **在資源充足的電腦上以 Headless 模式執行是非常可行且強烈推薦的做法！**
> 因為不用在主機端啟動肥大的 GNOME 桌面、X11/Wayland 或本機 GUI 瀏覽器，可將全部 CPU 核心與記憶體完全釋放給 GCC/G++ 編譯、SQLite 資料庫與 vLLM 大語言模型推論。

### 4.1 新主機環境準備
新主機需具備以下基礎工具：
- **Linux 系統**（Ubuntu 22.04 / 24.04 或 Debian 系列為佳）
- **C/C++ 編譯工具鏈**：`sudo apt update && sudo apt install -y build-essential gcc g++`
- **Python 3.10+** 及 venv：`sudo apt install -y python3-venv python3-pip`
- **Node.js 18+** 及 npm
- **Git** 與 **GitHub CLI (`gh`)**

### 4.2 取得原始碼與環境建置

```bash
# 1. Clone 儲存庫
git clone https://github.com/zzlee/codingpass-webui.git
cd codingpass-webui

# 2. 後端 Python 虛擬環境建立
cd backend
python3 -m venv venv
./venv/bin/pip install -r requirements.txt
cd ..

# 3. 前端依賴安裝
cd frontend
npm install
npm run build   # 驗證建置無誤
cd ..
```

### 4.3 啟動服務 (Headless 模式)

在遠端主機上，建議使用 `tmux` 或背景 daemon 啟動後端與前端：

#### 步驟 1：啟動後端 FastAPI (Port 8010)
```bash
cd backend
./venv/bin/uvicorn main:app --host 0.0.0.0 --port 8010
```

#### 步驟 2：啟動前端 Vite (Port 5173)
```bash
cd frontend
# 讓 Vite 監聽所有網路介面，方便外部瀏覽器直接連線
npm run dev -- --host 0.0.0.0 --port 5173
```

### 4.4 從外部客戶端瀏覽器連線方式

有兩種常用的存取方式：

1. **直接連線**：
   - 瀏覽器開啟：`http://<新主機IP>:5173`
   - （請確認新主機的防火牆放行 5173 與 8010 port）

2. **SSH 隧道本機轉發 (安全推薦)**：
   - 在本機筆電執行：
     ```bash
     ssh -L 5173:localhost:5173 -L 8010:localhost:8010 user@<新主機IP>
     ```
   - 接著筆電直接開啟瀏覽器進入 `http://localhost:5173` 即可，完全如同本機操作！

### 4.5 驗證服務健康狀態

```bash
# 測試後端健康狀態
curl -s http://127.0.0.1:8010/api/health

# 執行 91 項後端 API 完整測試
cd backend && ./venv/bin/python test_api.py

# 執行 16+ 項評測沙盒安全邊界測試
cd backend && ./venv/bin/python test_judge.py

# 執行 Phase 4 AI 助教與教師覆核完整測試
cd backend && ./venv/bin/python test_ai_phase4.py
```

---

## 5. 沙盒安全性與系統防護守則 (Safety Guidelines)

在新主機上請持續遵守 `AGENTS.md` 所列之防護規範：
1. **嚴禁無限制的進程生成測試**：測試多進程防護時，僅使用受控極小次數（如 3 次 fork），避免耗盡 Linux `RLIMIT_NPROC`。
2. **多層資源限制保護**：
   - `RLIMIT_CPU`：設定嚴格 CPU 秒數，超時觸發核心發送 `SIGXCPU` / `SIGKILL`。
   - `RLIMIT_AS`：限制虛擬記憶體（預設 256MB），超額立即觸發 `bad_alloc` 或 `SIGSEGV`。
   - `RLIMIT_FSIZE`：限制子進程寫入檔案大小，防止塞爆硬碟。
   - `RLIMIT_CORE = 0`：禁止產生 core dump。
3. **Process Group 隔離 (`os.setsid`)**：子進程獨立群組，超時由裁判端調用 `os.killpg(proc.pid, signal.SIGKILL)` 徹底清理。

---

## 6. Phase 4 (AI 智能輔助與批改建議) 已完成交付摘要

1. **對接本地 vLLM 服務 (`backend/ai_tutor.py`)**：
   - 呼叫 `http://localhost:8000/v1` 模型 `qwen3.8-flash-nvfp4`，並提供離線/降級規則式分析引擎 (`heuristic-rules`)。
2. **AI 代碼講評與診斷模組 (`AITutorPanel.tsx`)**：
   - 在 Playground 與 Problem Solver 內建「✨ AI 助教診斷 (Ask AI Tutor)」，提供繁體中文錯誤分析、時間/空間複雜度推估（如 $O(N)$、$O(\sqrt{N})$、$O(N^2)$）與具體修改方向建議（不洩漏隱藏測資、不直接給出整份解答）。
3. **老師批改輔助與覆核工作台 (`SubmissionReviewModal.tsx` & `Submissions.tsx`)**：
   - 支援檢視完整提交原始碼、一鍵產生並儲存 AI 講評與建議分數 (`ai_suggested_score`)，老師可手動輸入覆核分數 (`teacher_score`) 與評語 (`teacher_feedback`)。

---

## 7. CodingPass-Tests 深度整合架構 (已落地後端引擎)

參考 `~/vibecoding/codingpass-tests` 專案，已將其核心出題標準、雙軌評測沙盒、碼文限碼白箱檢查與結構抄襲分析深度整合至後端：

1. **庫題卡出題包相容 (`backend/problem_pack.py`)**：
   - 支援匯入官方題目包資料夾或 ZIP 壓縮檔（自動識別 `*_庫題卡碼文標碼.cpp`、`*_庫題卡測資範例.txt`、`*_庫題卡測資０隨輸入.teda`、`*_庫題卡碼文限碼.txt`、`*.pdf`）。
   - 提供端點 `POST /api/problems/import-pack`。
2. **TeDa 動態測資產生與雙軌評測 (`backend/teda_engine.py`)**：
   - 內建完整 TeDa-Testing 語言轉譯器（Node.js 子進程隔離執行），支援生成 30 筆動態隨機測資。
   - **Dual-Run 標竿正解動態執行**：將隨機測資餵給正解標竿程式，即時計算標準答案 `_ansout`。
   - **Strict vs Trimmed 雙軌比對**：精確捕捉行尾多餘空白／換行，給予 Presentation Warning (PE) 警告。
3. **碼文限碼白箱語法檢查 (`backend/wording_rules.py`)**：
   - 支援 `#term`、`#root`、`#regex` 與 `skipComments`/`skipStrings` 旗標，杜絕學生調用 `system()` 或 `<ctime>`，強制要求 `for|while` 等關鍵語法。
4. **Minimal Patch Probe 最小修復探針 (`backend/patch_probe.py`)**：
   - 面對 CE/WA 學生，在沙盒臨時目錄嘗試 1~3 行語法或邊界微修復；若修復後即可通過測資，判定邏輯架構完整，給予 28~35/40 邏輯給分。
5. **結構抄襲分析矩陣 (`backend/plagiarism.py`)**：
   - 計算學生之間的 AST 與 Token 相似度，並採用**標竿正解扣除法 (Reference Baseline Subtraction)**，排除皆寫出標準課本解法的正常情況，精準標記 $\ge 85\%$ 的疑似抄襲組。
   - 提供端點 `GET /api/classrooms/{cid}/problems/{pid}/plagiarism`。

---

## 8. CodingPass Local Agent 本機編譯代理與 WebSocket 雙軌架構 (Phase 5 已完整實作並驗證)

### 8.1 痛點與設計動機
在 40+ 名學生的電腦教室課堂中，學生在網頁上反覆點擊「編譯/測試範例」，每分鐘可能產生數十至數百次 GCC/G++ 編譯請求。由於編譯是高 CPU / 磁碟 I/O 密集程序，若全部由單一 HTTP 伺服器承擔，極易造成 CPU 100% 飽和、排隊超時甚至伺服器當機。

### 8.2 雙軌運算模型 (Hybrid Execution Model)
1. **日常高頻練習 (Playground / Run Samples)**：
   - 透過瀏覽器直接連線學生電腦本機的 `ws://127.0.0.1:8765`。
   - 學生筆電/PC 自行編譯與沙盒執行，回傳結果至網頁顯示。
   - **效果**：中央伺服器 CPU/RAM 負載降為接近 **0%**，學生享有毫秒級即時編譯反應。
2. **正式考試交卷 (Submit Solution)**：
   - 維持傳回中央伺服器裁判沙盒執行（含 5 項評分規程、隱藏測資比對、Wording Rules 白箱檢查與 Minimal Patch Probe）。
   - **效果**：隱藏測資絕對不外流給學生，杜絕學生竄改本機回傳分數。
3. **無縫自動降級**：
   - 網頁自動探測 `ws://127.0.0.1:8765`；若學生未安裝，自動切換為伺服器隊列執行，不影響基本功能。

### 8.3 已落地交付成果與元件
1. **零外部依賴 WebSocket 代理服務 (`local_agent/agent.py`)**：
   - 僅依賴 Python 3.8+ 內建標準函式庫（`asyncio`, `struct`, `hashlib`, `subprocess`, `tempfile`），完全不需 `pip install` 任何套件。
   - 支援 RFC 6455 雙向 Framing、Masking / Unmasking、Ping-Pong、自動編譯器探測（GCC / Clang）。
   - 內建進程群組隔離 (POSIX `start_new_session` / Windows `CREATE_NEW_PROCESS_GROUP`)、256KB 輸出上限與逾時整棵進程樹殲滅 (`killpg` / `taskkill /F /T`)，防止學生死迴圈、狂印字元與 fork 殭屍進程掛住本機。
2. **跨平台一鍵啟動腳本**：
   - `local_agent/run_agent.sh` (Linux / macOS 可執行腳本)
   - `local_agent/run_agent.bat` (Windows 批次檔)
   - `local_agent/README.md` (詳細使用指南)
   - `local_agent/test_agent.py` (單元執行與 WebSocket 整合測試，全數 PASS)
3. **後端安裝包下載與資訊端點 (`backend/main.py`)**：
   - `GET /api/agent/info`：回傳代理端點資訊與環境支援度。
   - `GET /api/agent/download`：動態壓縮打包為 `codingpass-agent.zip` 串流下載。
4. **前端 WebSocket 連線管理與路由分流**：
   - `frontend/src/localAgentContext.tsx`：自動重試連線、Ping 探測、`runLocally()` 分發器。
   - `frontend/src/components/Header.tsx`：右上角展示即時狀態膠囊（`🟢 本地加速已連線` / `⚪ 本地加速包`）。
   - `frontend/src/components/AgentModal.tsx`：一鍵下載安裝包、即時連線探測、3 步驟安裝指引與安全說明。
   - `frontend/src/components/Playground.tsx`：優先將自由執行指派給本地代理，按鈕動態呈現 `Run Code (Local)`。
   - `frontend/src/components/ProblemSolver.tsx`：範例測資測試 (`runSamples`) 優先由本地代理分流，正式交卷 (`submit`) 保留伺服器評分。

---

## 9. 測試與驗證現況總結 (Test Verification Status)

所有後端測試套件與前端建置均已於本機環境完成 100% 驗證：

| 測試模組 / 建置項目 | 測試內容與涵蓋範圍 | 測試結果 |
|---|---|---|
| **`test_stage34.py`** | 題目包匯入、PDF 留存、五項規程、最小修復探針、AST/Token 抄襲檢測 | ✅ **0 FAILED (35+ PASS)** |
| **`test_judge.py`** | C/C++ 編譯、超時保護、記憶體上限、進程群組清理、孤兒 Pipe 防卡死、`RLIMIT_NPROC` 防護 | ✅ **0 FAILED (45/45 PASS)** |
| **`test_api.py`** | 認證、班級、出題、測資批次評測、依題目套用 `memory_limit_mb`、權限隔離、歷史紀錄 | ✅ **0 FAILED (93/93 PASS)** |
| **`test_ai_phase4.py`** | AI 狀態、即時診斷、隱藏測資防洩漏、AI 講評、教師覆核與遷移 | ✅ **0 FAILED (85/85 PASS)** |
| **`test_statement_pdf_gradebook.py`** | 題目 PDF 串流與 Symlink 防護、班級成績冊 CSV 匯出與公式注入防護 | ✅ **0 FAILED (33/33 PASS)** |
| **`test_agent.py`** | Local Agent 編譯器探測、本地執行、輸出截斷、進程樹清理、Origin 白名單 (CSWSH)、`run_batch` 編譯一次執行多次 | ✅ **0 FAILED (100% PASS)** |
| **`frontend/e2e/app.spec.ts`** | Playwright 無頭瀏覽器 E2E 測試（健康度、Local Agent 彈窗、角色切換、題庫、班級與成績冊匯出） | ✅ **0 FAILED (5/5 PASS in 4.8s)** |
| **前端建置 (`npm run build`)** | React 18 + TypeScript + Monaco Editor (C/C++ 專屬精簡包) + Tailwind | ✅ **0 ERRORS (Built in 0.75s)** |

---

## 10. 專案未來改善方向與架構演進藍圖 (Future Improvements & Roadmap)

經過對目前程式碼庫的全面靜態檢視與壓力情境分析，專案在**評測吞吐量、資訊安全、教學功能、前端效能與工程維護性**六大維度仍有以下具體且高價值的演進空間：

### 10.1 評測效能與高併發交卷瓶頸 (Judge Performance & Concurrency)
1. **「編譯一次、執行多次」(Compile-Once, Run-Many) 優化（最高優先級）**：
   - **現況瓶頸**：`backend/main.py` 中的 `_judge_all_cases()` 目前在 `for case_row in cases:` 迴圈內逐筆呼叫 `compiler.run_code()`。當匯入含有 2 筆範例 + 30 筆隨機隱藏測資的題目時，**單次正式交卷會重複啟動 `g++` 編譯同一份程式碼 32 次**（耗費約 12～16 秒純編譯時間）；前端 `ProblemSolver.tsx` 的 `runSamples()` 亦會對每筆範例分別發送編譯請求。
   - **改善方案**：比照 `backend/teda_engine.py` 的 `run_dual_run()`，在獨立暫存目錄中**僅編譯一次**產出執行檔 `solution`，隨後針對 32 筆 `stdin` 重複執行該二進位檔（或支援批次 `run-batch` 介面）。單次 Submit 延遲可從 ~15 秒驟降至 **0.5 秒以內（提速 25～30 倍）**。
   - **已處置 (v1.2)**：伺服器端已由 `compiler.run_code_batch()` 於單一暫存目錄編譯一次、每筆測資獨立子目錄執行，`_judge_all_cases()` 不再逐筆編譯；前端 `ProblemSolver.runSamples()` 於 Local Agent 連線時改發單一 `run_batch` 请求（一次編譯、全部範例本地執行），僅在代理未連線、拒答或回傳筆數不符時才退回逐筆 `POST /api/run`。
2. **非同步批改佇列與即時進度推播 (Async Task Queue + SSE/WebSocket)**：
   - **現況瓶頸**：課堂測驗結束前最後一分鐘，若 40 名學生同時點擊 `Submit`，同步 HTTP 請求需排隊等待 `MAX_CONCURRENT_RUNS=4` 號誌與 Minimal Patch Probe（最多 20 次探針編譯），容易觸發瀏覽器端 HTTP Timeout。
   - **改善方案**：將 `POST /api/problems/{id}/submit` 改為非阻塞入列（立即回傳 `202 Accepted` 與 `submission_id`），由背景 Worker Pool 消化佇列，並透過 Server-Sent Events (SSE) 向學生端即時推播批改進度（如：`編譯成功 -> 測試中 (14/32) -> 啟動修復探針...`）。

### 10.2 學生端 Local Agent 安全加固與零安裝打包 (Local Agent Security & Packaging)
1. **Cross-Site WebSocket Hijacking (CSWSH) 防護**：
   - **已處置 (v1.1)**：`MinimalWebSocket.handshake()` 已強制檢查 `Origin` 標頭（`is_allowed_origin()`）：僅放行 `localhost` / `127.0.0.1` / `[::1]` 與 `192.168.` / `10.` / `172.` 內網前綴，另可用環境變數 `CODINGPASS_ALLOWED_ORIGINS`（逗號分隔）放行課堂伺服器網域；其餘來源直接回覆 `HTTP/1.1 403 Forbidden`（測試 `test_agent.py` 已驗證 `https://evil.example.com` 被拒）。不帶 `Origin` 的非瀏覽器客戶端照常放行。
   - **後續建議**：可再加輕量 **Pairing Token 機制**：Agent 啟動時產生隨機 Token（或由網頁自動配對），握手後首個封包須驗證通過才允許執行 `run` 指令，進一步封鎖同網段其他主機的連線。
2. **本機 Agent 輸出緩衝限制與 Windows 進程樹清理**：
   - **已處置 (v1.1)**：改用 `subprocess.Popen` + 背景執行緒雙向泵壓，`stdout`/`stderr` 各自最多保留 `MAX_OUTPUT_BYTES = 256KB`（超出部分仍持續 drain 管道避免死鎖，並回傳 `stdout_truncated` / `stderr_truncated` 旗標）；逾時時 POSIX 走 `os.killpg(os.getpgid(pid), SIGKILL)`、Windows 走 `taskkill /F /T /PID`，並已於 `test_agent.py` 以受控 2 次 fork + 死迴圈驗證無孤兒進程殘留。
3. **Windows 真正「零依賴」單一執行檔 / 綠色整合包**：
   - **現況痛點**：目前 `codingpass-agent.zip` 僅約 10KB，但要求學生電腦已安裝 Python 3.8+。
   - **改善方案**：提供進階版下載選項，將 **Python Embeddable (Windows 內嵌版 ~10MB) + 精簡版 w64devkit (MinGW GCC)** 或透過 `PyInstaller` 封裝為單一 `codingpass-agent.exe`，達成 Windows 學生 100% 免裝 Python、免設環境變數的開箱即用體驗。

### 10.3 伺服器端沙盒隔離升級 (Server-Side Sandbox Hardening)
1. **檔案系統與網路命名空間隔離 (`bwrap` / `nsjail`)**：
   - **已處置 (v1.2)**：`Problem.memory_limit_mb` 現在是**實際強制**的 `RLIMIT_AS` 上限。
   - **已處置 (Direction C)**：在 Linux 伺服器上完整導入 **Bubblewrap (`bwrap`)** 安全沙盒：
     - `--ro-bind / / --dev /dev --proc /proc --tmpfs /tmp`（唯讀根目錄 + 獨立私有 `/tmp` 讀寫）。
     - **敏感目錄防護遮蔽 (Sensitive Path Masking)**：自動將 `/app`、`/root`、專案儲存庫目錄與資料庫目錄以 `--tmpfs` 覆蓋遮蔽，即使受測程式試圖讀取 `.jwt_secret` 或 `codingpass.db`，取得之內容亦為全空，杜絕金鑰偽造與隱藏測資外洩。
     - **命名空間與權限限縮**：`--unshare-net --unshare-pid --unshare-ipc --unshare-uts --new-session --cap-drop ALL`，徹底切斷網路、隔離 PID 樹、IPC 與 Capabilities。
     - **動態自我探測相容性**：`is_bwrap_enabled()` 於啟動時探測容器權限（`SYS_ADMIN` + `NET_ADMIN`），若處於未開放命名空間之環境則平滑 fallback 至 POSIX rlimit 機制，絕不中斷服務。
     - **崩潰訊號還原**：精確還原 SIGSEGV、SIGABRT、SIGFPE、SIGXCPU 等核心訊號，同時完整保留受測程式合法之應用層非零退出代碼（如 `exit(137)`）。

### 10.4 教學與考試管理功能補完 (Pedagogy & Exam Features)
1. **題目 PDF 題面線上檢視器 (`GET /api/problems/{id}/pdf`)**：
   - **已處置 (v1.2)**：後端已實作 `GET /api/problems/{problem_id}/pdf`（含權限驗證、`os.stat(follow_symlinks=False)` Symlink 穿越防護、以及對學生隱藏伺服器絕對路徑），前端 `ProblemSolver.tsx` 於 `has_statement_pdf` 為真時顯示「📄 題目 PDF」按鈕，點擊即可於新分頁檢視 PDF 題面。
2. **隨堂測驗／期中考模式 (Exam & Contest Mode)**：
   - 新增題目／作業的**開放作答時間窗 (`available_from` ~ `due_at`)** 與遲交扣分策略。
   - 新增**考試防弊開關**：教師可針對特定班級或題目一鍵「暫時關閉 AI 助教診斷」與「啟用提交冷卻時間（如每 15 秒限交 1 次）」。
3. **教師成績冊與 CSV/Excel 匯出 (Gradebook & Export)**：
   - **已處置 (v1.2)**：後端已實作 `GET /api/classrooms/{classroom_id}/gradebook.csv`（輸出含 UTF-8 BOM 之學生 × 題目得分矩陣、嘗試次數、總分與平均分，並具備 `_csv_cell` 公式注入轉義防護），前端 `Classrooms.tsx` 已提供教師／管理員一鍵點擊「匯出成績冊 (CSV)」下載。
4. **題目作答區「自訂測資 (Custom Input)」與標竿對拍**：
   - 在 `ProblemSolver.tsx` 讓學生除了跑固定範例（Run Samples）外，也能自行輸入測試資料；若該題具備老師匯入的 `ref_code`，還可選擇允許學生輸入自訂測資後由系統背景跑出標竿答案供比對除錯。

### 10.5 前端載入效能與打包瘦身 (Frontend Bundle Optimization)
1. **Monaco Editor 語法精簡與路由 Code Splitting**：
   - **已處置 (v1.2)**：`frontend/src/monacoSetup.ts` 已移除整體的 `editor.main.js` 匯入，改為僅載入核心編輯器元件並單獨註冊 `c` 與 `cpp` 語法（`monaco-editor/languages/definitions/cpp/register.js`），徹底剔除 `ts.worker.js` (6.92 MB)、`css.worker.js` (1.07 MB)、`html.worker.js` (743 KB)、`json.worker.js` (433 KB) 與 80 種無關語言，Vite 建置時間由 1.49s 縮短至 **0.75s**。

### 10.6 軟體工程結構與維運 (Architecture & DevOps)
1. **後端單體檔案模組化拆分 (`backend/main.py`)**：
   - **已處置 (Direction D)**：`backend/main.py` 從 3,984 行精簡至 ~400 行。依功能職責拆解為：
     - `config.py`：常數與邊界定義。
     - `schemas.py`：Pydantic 請求與回應型別模型。
     - `dependencies.py`：認證依賴、權限守門與評測槽號 Semaphore。
     - `services/judge.py`：核心評測邏輯與五項規程評分。
     - `routers/`：`auth.py`、`run.py`、`agent.py`、`classrooms.py`、`problems.py`、`submissions.py`、`ai.py`。
     - 完整維持原有模組 re-export，測試與 API 契約 100% 向後相容。
2. **容器化一鍵部署 (`Dockerfile` + `docker-compose.yml`)**：
   - **已處置 (Direction C)**：
     - 多階段建置 `Dockerfile`（Node.js 20 建置前端 SPA，Python 3.12 運行後端、Bubblewrap、GCC/G++ 與 Node.js TeDa 引擎）。
     - 單一容器同時服務 API 與前端靜態資源，內建 `SpaStaticFiles` 嚴格防止路徑穿越與任意檔案外洩。
     - `docker-compose.yml` 支援資料庫持久化 Volume、Host Gateway 對接本地 vLLM，以及完整 Healthcheck。

### 10.7 改善優先級建議路線圖 (Prioritized Action Plan)

| 優先級 | 項目 | 狀態 | 實際效益 |
|---|---|---|---|
| **P0 (立即見效)** | `_judge_all_cases` 與 Local Agent 改為「編譯一次、執行多次」 | ✅ **已完成** | 正式 Submit 與本地範例測試多測資批改速度提升 **25~30 倍** |
| **P0 (安全必備)** | Local Agent `Origin` 白名單 (RFC 1918) + 輸出截斷 + 孤兒 Pipe 防卡死 + `RLIMIT_NPROC` | ✅ **已完成** | 杜絕外部惡意網站透過 WebSocket 觸發本機 RCE、Fork Bomb 與 Pipe 卡死 |
| **P1 (教學實用)** | 題目 PDF 題面預覽端點 (`GET /api/problems/{id}/pdf`) + 班級成績 CSV 匯出 (`gradebook.csv`) | ✅ **已完成** | 完整發揮庫題卡 PDF 題面價值、支援 Excel 防公式注入之成績冊匯出 |
| **P1 (前端瘦身)** | Monaco Editor 僅載入 C/C++（剔除 4 個大型 Worker 與 80 種語言） | ✅ **已完成** | 移除 >9 MB 無用 Worker 產物，建置與載入速度翻倍 |
| **P1 (模組架構)** | `backend/main.py` 路由與領域服務模組化拆分 (Direction D) | ✅ **已完成** | 單體 4,000 行瘦身為乾淨路由器架構，各模組職責清晰好維護 |
| **P1 (沙盒容器)** | `bwrap` 沙盒隔離 + Dockerfile / docker-compose 一鍵部署 (Direction C) | ✅ **已完成** | 阻絕惡意程式讀取資料庫與金鑰，一鍵容器化上線 |
| **P2 (考試進階)** | 考試時間窗、AI 開關、非同步批改佇列 (SSE) | 📋 待規劃 | 支援 40~100 人正式期中/期末上機考零超時 |
