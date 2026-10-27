"""Comprehensive End-to-end Playwright Test for CodingPass Phase 2 & 3."""
import sys
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

BASE = "http://127.0.0.1:5173"
results = []

def check(name, ok, extra=""):
    results.append((name, bool(ok), extra))
    status_str = "PASS  " if ok else "FAIL  "
    print(f"{status_str}{name}" + (f"  [{extra}]" if extra else ""), flush=True)

def main():
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1600, "height": 950})
        errors = []
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        page.on("pageerror", lambda e: errors.append(str(e)))

        print("Navigating to CodingPass WebUI...", flush=True)
        page.goto(BASE, wait_until="networkidle")
        page.wait_for_selector(".monaco-editor", timeout=20000)

        # 1. Branding & Health
        check("Brand logo visible", page.get_by_text("CodingPass").first.is_visible())
        page.wait_for_selector("text=Judge online", timeout=10000)
        check("Judge online badge visible", page.get_by_text("Judge online").first.is_visible())

        # 2. Tabs navigation
        playground_tab = page.locator("button, a").filter(has_text="Playground").first
        problems_tab = page.locator("button, a").filter(has_text="Problem Bank").first
        classrooms_tab = page.locator("button, a").filter(has_text="Classrooms").first
        submissions_tab = page.locator("button, a").filter(has_text="Submissions").first

        check("Playground tab present", playground_tab.is_visible())
        check("Problem Bank tab present", problems_tab.is_visible())
        check("Classrooms tab present", classrooms_tab.is_visible())
        check("Submissions tab present", submissions_tab.is_visible())

        # 3. Playground Run Code
        check("Playground Run Code CTA visible", page.get_by_role("button", name="Run Code").is_visible())

        # 4. Auth: Quick Login as Alice (student1)
        account_btn = page.locator("button[aria-haspopup='menu']").first
        check("Account menu button visible", account_btn.is_visible())
        account_btn.click()
        page.wait_for_timeout(400)
        
        # Click Alice
        alice_opt = page.locator("button[role='menuitem']").filter(has_text="Alice").first
        check("Alice demo switcher visible", alice_opt.is_visible())
        alice_opt.click()
        page.wait_for_timeout(1000)

        # Check logged in as student
        check("Alice profile badge", page.get_by_text("Alice").first.is_visible())

        # 5. Problem Bank Navigation
        problems_tab.click()
        page.wait_for_timeout(1000)
        check("Problem list: Two Sum visible", page.get_by_text("Two Sum (A + B Problem)").first.is_visible())
        check("Problem list: Prime Number visible", page.get_by_text("Check Prime Number").first.is_visible())

        # 6. Open Problem Solver
        two_sum_card = page.get_by_text("Two Sum (A + B Problem)").first
        two_sum_card.click()
        page.wait_for_timeout(1500)

        # Verify solver interface
        check("Solver Monaco editor loaded", page.locator(".monaco-editor").count() > 0)
        submit_btn = page.get_by_role("button", name="Submit").or_(page.locator("button").filter(has_text="Submit Solution")).first
        run_samples_btn = page.get_by_role("button", name="Run Samples").or_(page.locator("button").filter(has_text="Run Samples")).first
        check("Run Samples CTA visible", run_samples_btn.is_visible())
        check("Submit CTA visible", submit_btn.is_visible())

        # 7. Run Samples on Two Sum starter code (already contains A+B solution)
        print("Clicking Submit Solution...", flush=True)
        submit_btn.click()
        page.wait_for_selector("text=ACCEPTED", timeout=25000)
        check("Judge Verdict ACCEPTED", page.get_by_text("ACCEPTED").first.is_visible())
        check("Score 100%", page.get_by_text("100%").first.is_visible())

        # 8. Classrooms Tab (Student View)
        classrooms_tab.click()
        page.wait_for_timeout(1000)
        check("Enrolled classroom CS101 visible", page.get_by_text("CS101").first.is_visible())
        check("Join Classroom CTA visible", page.get_by_role("button", name="Join Classroom").or_(page.get_by_text("Join Classroom")).first.is_visible())

        # 9. Submissions Tab (Student View)
        submissions_tab.click()
        page.wait_for_timeout(1000)
        check("Submissions record list visible", page.get_by_text("Two Sum (A + B Problem)").first.is_visible())
        check("Submission verdict badge in history", page.get_by_text("ACCEPTED").first.is_visible())

        # 10. Switch to Teacher (Prof. Chen)
        account_btn = page.locator("button[aria-haspopup='menu']").first
        account_btn.click()
        page.wait_for_timeout(400)
        teacher_opt = page.locator("button[role='menuitem']").filter(has_text="Prof. Chen").first
        teacher_opt.click()
        page.wait_for_timeout(1000)
        check("Prof. Chen logged in", page.get_by_text("Prof. Chen").first.is_visible())

        # 11. Teacher View in Problem Bank
        problems_tab.click()
        page.wait_for_timeout(1000)
        create_prob_btn = page.get_by_role("button", name="Create Problem").or_(page.get_by_text("Create Problem")).first
        check("Teacher Create Problem CTA visible", create_prob_btn.is_visible())

        # 12. Teacher View in Classrooms
        classrooms_tab.click()
        page.wait_for_timeout(1000)
        create_class_btn = page.get_by_role("button", name="Create Classroom").or_(page.get_by_text("Create Classroom")).first
        check("Teacher Create Classroom CTA visible", create_class_btn.is_visible())
        check("Teacher sees CS101 roster details", page.get_by_text("CS101").first.is_visible())

        # Filter out minor harmless warnings
        real_errors = [e for e in errors if "favicon" not in e and "Download the React DevTools" not in e]
        check("Zero unhandled console errors", len(real_errors) == 0, "; ".join(real_errors)[:200])

        browser.close()

    failed = [name for name, ok, _ in results if not ok]
    print("\n================== E2E SUMMARY ==================")
    print(f"{len(results) - len(failed)}/{len(results)} checks passed")
    if failed:
        print("FAILED CHECKS:", ", ".join(failed))
        sys.exit(1)
    else:
        print("ALL END-TO-END CHECKS PASSED PERFECTLY!")

if __name__ == "__main__":
    main()
