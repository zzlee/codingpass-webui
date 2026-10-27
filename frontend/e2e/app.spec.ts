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
})
