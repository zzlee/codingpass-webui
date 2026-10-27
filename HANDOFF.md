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
   - 內建進程群組隔離 (`os.setsid`) 與超時終止保護，防止學生死迴圈掛住。
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
| **`test_judge.py`** | C/C++ 編譯、超時保護、記憶體上限、進程群組清理、殭屍進程防護 | ✅ **0 FAILED (28/28 PASS)** |
| **`test_api.py`** | 認證、班級、出題、測資批次評測、權限隔離、歷史紀錄 | ✅ **0 FAILED (91/91 PASS)** |
| **`test_ai_phase4.py`** | AI 狀態、即時診斷、隱藏測資防洩漏、AI 講評、教師覆核與遷移 | ✅ **0 FAILED (85/85 PASS)** |
| **`test_agent.py`** | Local Agent 編譯器探測、本地執行、RFC 6455 WebSocket 協議整合 | ✅ **0 FAILED (100% PASS)** |
| **前端建置 (`npm run build`)** | React 18 + TypeScript + Monaco Editor + Tailwind | ✅ **0 ERRORS (Built in 1.4s)** |
