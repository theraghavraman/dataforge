"""Use an explicitly configured Chromium, a system binary, or Playwright's bundle."""
import os
import shutil

def launch_browser(playwright):
    executable = os.getenv("PLAYWRIGHT_CHROMIUM_EXECUTABLE") or shutil.which("chromium") or shutil.which("chromium-browser")
    options = {"headless": True}
    if executable:
        options["executable_path"] = executable
    # Only the root-owned disposable QA container requires this switch.
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        options["args"] = ["--no-sandbox"]
    return playwright.chromium.launch(**options)
