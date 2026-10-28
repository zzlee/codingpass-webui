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

  test('Student custom test case debug execution in ProblemSolver', async ({ page }) => {
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

    // Verify CustomTestPanel is displayed
    const stdinArea = page.locator('[data-testid="custom-stdin-input"]')
    await expect(stdinArea).toBeVisible({ timeout: 5000 })

    // Clear and type custom test input: "15 27\n"
    await stdinArea.fill('15 27\n')

    // Click Run Custom Input and wait for /api/run response
    const runBtn = page.locator('[data-testid="btn-run-custom"]')
    await expect(runBtn).toBeVisible()
    await Promise.all([
      page.waitForResponse((res) => res.url().includes('/api/run') && res.status() === 200, { timeout: 15000 }),
      runBtn.click(),
    ])

    // Verify execution completes with SUCCESS and stdout is visible
    await expect(page.getByText('SUCCESS', { exact: true })).toBeVisible({ timeout: 10000 })
    await expect(page.getByText('標準輸出 (Stdout)')).toBeVisible()

    // Switch back to "範例評測與成績" tab
    const samplesTab = page.locator('[data-testid="tab-samples"]')
    await samplesTab.click()
    await expect(page.getByText('No result yet')).toBeVisible()
  })
})
