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
})
