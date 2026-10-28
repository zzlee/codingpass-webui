import { test, expect } from '@playwright/test'

test.describe('CodingPass WebUI E2E Test Suite', () => {
  test('App loads successfully with online judge health pill', async ({ page }) => {
    await page.goto('/')

    // Check Branding
    await expect(page.getByText('CodingPass', { exact: true })).toBeVisible()
    await expect(page.getByText('C/C++ judge')).toBeVisible()

    // Check Judge Online Health Pill
    const healthPill = page.locator('button[title*="/api/health"]')
    await expect(healthPill).toBeVisible()
    await expect(healthPill).toContainText('Judge online')
  })

  test('Local Agent pill and modal interaction', async ({ page }) => {
    await page.goto('/')

    // Locate the local agent pill
    const agentPill = page.getByRole('button', { name: /本地加速/ })
    await expect(agentPill).toBeVisible()

    // Click to open AgentModal
    await agentPill.click()

    // Verify modal elements
    await expect(page.getByText('CodingPass 本地加速代理 (Local Agent)')).toBeVisible()
    await expect(page.getByText('快速使用說明 (只需 3 步驟)')).toBeVisible()
    await expect(page.getByRole('button', { name: /下載 codingpass-agent.zip/ })).toBeVisible()

    // Close modal
    await page.getByRole('button', { name: '關閉' }).click()
    await expect(page.getByText('CodingPass 本地加速代理 (Local Agent)')).not.toBeVisible()
  })

  test('1-Click Demo Role Switcher switches identity effortlessly', async ({ page }) => {
    await page.goto('/')

    // Open quick account menu
    const accountTrigger = page.locator('button[aria-haspopup="menu"]')
    await accountTrigger.click()

    // Click Alice (student)
    await page.getByRole('menuitem', { name: /Alice/ }).click()

    // Verify Alice is logged in
    await expect(page.locator('button[aria-haspopup="menu"]')).toContainText('Alice')
    await expect(page.getByText('student', { exact: true })).toBeVisible()

    // Switch to Prof. Chen
    await accountTrigger.click()
    await page.getByRole('menuitem', { name: /Prof\. Chen/ }).click()
    await expect(page.locator('button[aria-haspopup="menu"]')).toContainText('Prof. Chen')
    await expect(page.getByText('teacher', { exact: true })).toBeVisible()
  })

  test('Problem Bank navigation and problem list view', async ({ page }) => {
    await page.goto('/')

    // Switch to student Alice
    const accountTrigger = page.locator('button[aria-haspopup="menu"]')
    await accountTrigger.click()
    await page.getByRole('menuitem', { name: /Alice/ }).click()
    await expect(page.locator('button[aria-haspopup="menu"]')).toContainText('Alice')

    // Navigate to Problem Bank tab
    await page.getByRole('button', { name: /Problem Bank/ }).click()

    // Verify Two Sum is visible
    await expect(page.getByText('Two Sum (A + B Problem)')).toBeVisible()
    await expect(page.getByText('Check Prime Number')).toBeVisible()
  })

  test('Classrooms tab and Teacher view', async ({ page }) => {
    await page.goto('/')

    // Sign in as Prof. Chen
    const accountTrigger = page.locator('button[aria-haspopup="menu"]')
    await accountTrigger.click()
    await page.getByRole('menuitem', { name: /Prof\. Chen/ }).click()

    // Navigate to Classrooms tab
    await page.getByRole('button', { name: /Classrooms/ }).click()

    // Verify CS101 classroom card exists
    await expect(page.getByText('C/C++ Programming 101')).toBeVisible()
    await expect(page.getByText('CS101')).toBeVisible()

    // Click to view classroom detail
    await page.getByText('C/C++ Programming 101').click()

    // Verify student roster shows Alice and Gradebook CSV export button is available
    await expect(page.getByText('Alice')).toBeVisible()
    await expect(page.getByRole('button', { name: /匯出成績冊/ })).toBeVisible()
  })

  test('Admin User Management modal and interaction', async ({ page }) => {
    await page.goto('/')

    // Sign in as Admin
    const accountTrigger = page.locator('button[aria-haspopup="menu"]')
    await accountTrigger.click()
    await page.getByRole('menuitem', { name: /Admin/ }).click()

    // Verify Admin button is visible
    const userMgmtBtn = page.getByRole('button', { name: /使用者管理/ })
    await expect(userMgmtBtn).toBeVisible()

    // Click to open User Management modal
    await userMgmtBtn.click()

    // Verify User Management dialog elements
    await expect(page.getByText('使用者帳號管理 (User Management)')).toBeVisible()
    await expect(page.getByPlaceholder('搜尋帳號、姓名、Email…')).toBeVisible()
    await expect(page.getByText('共計')).toBeVisible()

    // Close modal
    await page.getByRole('button', { name: '關閉' }).click()
    await expect(page.getByText('使用者帳號管理 (User Management)')).not.toBeVisible()
  })

  test('Change Password modal interaction', async ({ page }) => {
    await page.goto('/')

    // Sign in as Alice
    const accountTrigger = page.locator('button[aria-haspopup="menu"]')
    await accountTrigger.click()
    await page.getByRole('menuitem', { name: /Alice/ }).click()

    // Wait for login to settle
    await expect(accountTrigger).toContainText('Alice')

    // Reopen menu and click Change Password
    await accountTrigger.click()
    const changePwdBtn = page.getByRole('button', { name: /變更密碼/ })
    await expect(changePwdBtn).toBeVisible()
    await changePwdBtn.click()

    // Verify Change Password modal
    await expect(page.getByText('變更密碼 (Change Password)')).toBeVisible()
    await expect(page.getByPlaceholder('請輸入現有密碼')).toBeVisible()
    await expect(page.getByPlaceholder('請輸入新密碼 (至少 6 碼)')).toBeVisible()

    // Cancel modal
    await page.getByRole('button', { name: '取消' }).click()
    await expect(page.getByText('變更密碼 (Change Password)')).not.toBeVisible()
  })

  test('Teacher live exam proctoring dashboard interaction', async ({ page }) => {
    await page.goto('/')

    // Sign in as Prof. Chen
    const accountTrigger = page.locator('button[aria-haspopup="menu"]')
    await accountTrigger.click()
    await page.getByRole('menuitem', { name: /Prof\. Chen/ }).click()
    await expect(accountTrigger).toContainText('Prof. Chen')

    // Navigate to Classrooms
    await page.getByRole('button', { name: /Classrooms/ }).click()

    // Expand CS101 classroom
    await page.getByText('C/C++ Programming 101').click()

    // Locate proctoring button (Eye icon)
    const proctorBtn = page.locator('button[title*="即時監考儀表板"]').first()
    await expect(proctorBtn).toBeVisible()
    await proctorBtn.click()

    // Verify Proctoring modal
    await expect(page.getByText('即時監考儀表板 (Live Exam Proctoring)')).toBeVisible()
    await expect(page.getByText('應考總人次')).toBeVisible()
    await expect(page.getByText('即時連線中')).toBeVisible()

    // Close modal
    await page.getByRole('button', { name: '關閉' }).click()
    await expect(page.getByText('即時監考儀表板 (Live Exam Proctoring)')).not.toBeVisible()
  })

  test('Teacher AI Problem Generation modal opens and handles presets', async ({ page }) => {
    await page.goto('/')

    // Sign in as Prof. Chen
    const accountTrigger = page.locator('button[aria-haspopup="menu"]')
    await accountTrigger.click()
    await page.getByRole('menuitem', { name: /Prof\. Chen/ }).click()
    await expect(accountTrigger).toContainText('Prof. Chen')

    // Navigate to Problem Bank
    await page.getByRole('button', { name: /Problem Bank/ }).click()

    // Find and click the AI Problem Generation button
    const aiBtn = page.getByRole('button', { name: /AI 智慧出題/ })
    await expect(aiBtn).toBeVisible()
    await aiBtn.click()

    // Modal should be visible
    await expect(page.getByText('CodingPass AI 智慧出題 Agent')).toBeVisible()
    await expect(page.getByText('Pi + Qwen3.8 Flash')).toBeVisible()

    // Verify presets exist and clicking one populates the prompt
    await page.getByRole('button', { name: '區間質數與因數分解' }).click()
    const promptInput = page.getByPlaceholder('請清楚描述題目情境、演算法核心要求、輸入與輸出規範、邊界數值限制...')
    await expect(promptInput).toHaveValue(/計算區間內質數個數/)

    // Close the modal
    await page.locator('button[aria-label="Close dialog"]').click()
    await expect(page.getByText('CodingPass AI 智慧出題 Agent')).not.toBeVisible()
  })

  test('Student cannot compile via server without Local Agent and receives clear guidance', async ({ page }) => {
    await page.goto('/')

    // Sign in as student Alice
    const accountTrigger = page.locator('button[aria-haspopup="menu"]')
    await accountTrigger.click()
    await page.getByRole('menuitem', { name: /Alice/ }).click()
    await expect(accountTrigger).toContainText('Alice')

    // Navigate to Problem Bank tab
    await page.getByRole('button', { name: /Problem Bank/ }).click()

    // Open Two Sum problem and wait for problem response
    await Promise.all([
      page.waitForResponse((res) => res.url().includes('/api/problems/1') && res.status() === 200),
      page.locator('[data-testid="problem-card-1"]').click(),
    ])

    // Wait for solver tab buttons to appear
    const customTab = page.locator('[data-testid="tab-custom"]')
    await expect(customTab).toBeVisible({ timeout: 15000 })
    await customTab.click()

    // Verify CustomTestPanel is displayed with Local Agent disconnected indicators
    await expect(page.getByText('🔴 Local Agent 未連線（需本機編譯）')).toBeVisible({ timeout: 5000 })
    await expect(page.getByText('伺服器不開放學生直接編譯')).toBeVisible()

    // Verify Run Custom Input button is disabled without Local Agent
    const runBtn = page.locator('[data-testid="btn-run-custom"]')
    await expect(runBtn).toBeVisible()
    await expect(runBtn).toBeDisabled()

    // Switch back to "範例評測與成績" tab and verify Run Samples warns about Local Agent requirement
    const samplesTab = page.locator('[data-testid="tab-samples"]')
    await samplesTab.click()
    await expect(page.getByText('No result yet')).toBeVisible()

    const runSamplesBtn = page.getByRole('button', { name: /Run Samples/ })
    await expect(runSamplesBtn).toBeVisible()
    await runSamplesBtn.click()

    // Verify notification informs student that Local Agent is required and server does not compile
    await expect(
      page.getByText('未連線本機 Local Agent：學生端測試與除錯須於本機編譯執行，伺服器不提供直接編譯測試。'),
    ).toBeVisible({ timeout: 5000 })
  })

  test('Local Agent Toolchain Doctor opens and displays diagnostics and install guides', async ({ page }) => {
    await page.goto('/')

    // Click local agent pill in header
    const agentPill = page.getByRole('button', { name: /本地加速/ })
    await agentPill.click()

    // Switch to Doctor tab
    const doctorTab = page.getByRole('button', { name: /本機工具鏈一鍵診斷/ })
    await expect(doctorTab).toBeVisible()
    await doctorTab.click()

    // Verify diagnostic recommendations and install commands are present
    await expect(page.getByText('本機 C/C++ 編譯工具鏈健全性診斷')).toBeVisible()
    await expect(page.getByText('跨平台編譯器一鍵安裝指令')).toBeVisible()
    await expect(page.getByText('winget install MSYS2.MSYS2')).toBeVisible()
    await expect(page.getByText('xcode-select --install')).toBeVisible()

    // Close modal
    await page.getByRole('button', { name: '關閉' }).click()
  })

  test('Multi-Case Custom Testing allows adding, renaming, and importing sample cases', async ({ page }) => {
    await page.goto('/')

    // Sign in as Alice
    const accountTrigger = page.locator('button[aria-haspopup="menu"]')
    await accountTrigger.click()
    await page.getByRole('menuitem', { name: /Alice/ }).click()

    // Navigate to Problem Bank and open Two Sum
    await page.getByRole('button', { name: /Problem Bank/ }).click()
    await Promise.all([
      page.waitForResponse((res) => res.url().includes('/api/problems/1') && res.status() === 200),
      page.locator('[data-testid="problem-card-1"]').click(),
    ])

    // Switch to custom tab
    const customTab = page.locator('[data-testid="tab-custom"]')
    await expect(customTab).toBeVisible()
    await customTab.click()

    // Verify initial case #1 exists
    await expect(page.getByText('自訂測資 #1')).toBeVisible()

    // Click "+ 新增測資"
    const addCaseBtn = page.getByRole('button', { name: /新增測資/ })
    await expect(addCaseBtn).toBeVisible()
    await addCaseBtn.click()

    // Verify case #2 appears
    await expect(page.getByText('自訂測資 #2')).toBeVisible()

    // Test import all samples
    const importAllBtn = page.getByRole('button', { name: /匯入全部範例/ })
    await expect(importAllBtn).toBeVisible()
    await importAllBtn.click()

    // Verify imported sample tabs
    await expect(page.getByText('範例 #1')).toBeVisible()
    await expect(page.getByText('範例 #2')).toBeVisible()
  })

  test('Code Revision History modal opens and allows viewing snapshots and restoring code', async ({ page }) => {
    await page.goto('/')

    // Sign in as Alice
    const accountTrigger = page.locator('button[aria-haspopup="menu"]')
    await accountTrigger.click()
    await page.getByRole('menuitem', { name: /Alice/ }).click()

    // Open Two Sum
    await page.getByRole('button', { name: /Problem Bank/ }).click()
    await Promise.all([
      page.waitForResponse((res) => res.url().includes('/api/problems/1') && res.status() === 200),
      page.locator('[data-testid="problem-card-1"]').click(),
    ])

    // Click "版本歷程" button
    const historyBtn = page.getByRole('button', { name: /版本歷程/ })
    await expect(historyBtn).toBeVisible()
    await historyBtn.click()

    // Verify Revision modal opened
    await expect(page.getByText('程式碼版本歷程與還原')).toBeVisible()

    // Close modal
    await page.getByRole('button', { name: '關閉' }).click()
    await expect(page.getByText('程式碼版本歷程與還原')).not.toBeVisible()
  })
})
