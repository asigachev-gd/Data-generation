"""Browser verification of the complete persisted-data user journey.

Run with ``make test-e2e`` after ``python -m playwright install chromium``.  The
suite starts a clean Compose project and uses the explicit deterministic test
double, so it neither calls Gemini nor requires Vertex credentials.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.e2e

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "tests" / "fixtures" / "library_mgm_postgresql.sql"
COMPOSE = ROOT / "tests" / "e2e" / "compose.e2e.yaml"
PROJECT = "data_generation_e2e"


def _compose(*args: str) -> None:
    subprocess.run(
        [
            "docker",
            "compose",
            "--project-name",
            PROJECT,
            "--file",
            str(ROOT / "compose.yaml"),
            "--file",
            str(COMPOSE),
            *args,
        ],
        cwd=ROOT,
        check=True,
        env={
            **os.environ,
            "APP_PORT": "0",
            "POSTGRES_PASSWORD": "e2e-test-password",
        },
    )


def _app_url() -> str:
    published = subprocess.check_output(
        [
            "docker",
            "compose",
            "--project-name",
            PROJECT,
            "--file",
            str(ROOT / "compose.yaml"),
            "--file",
            str(COMPOSE),
            "port",
            "app",
            "8501",
        ],
        cwd=ROOT,
        env={**os.environ, "APP_PORT": "0", "POSTGRES_PASSWORD": "e2e-test-password"},
        text=True,
    ).strip()
    return f"http://127.0.0.1:{published.rsplit(':', maxsplit=1)[-1]}"


@pytest.fixture(scope="module")
def browser_page():  # type: ignore[no-untyped-def]
    if shutil.which("docker") is None:
        pytest.skip("Docker is required for browser end-to-end verification.")
    playwright = pytest.importorskip("playwright.sync_api")
    # Recover from an interrupted prior browser run before claiming the fixed Compose project name.
    _compose("down", "--volumes", "--remove-orphans")
    _compose("up", "--build", "--detach", "--wait")
    url = _app_url()
    try:
        with playwright.sync_playwright() as runner:
            try:
                browser = runner.chromium.launch()
            except Exception as error:
                pytest.skip(f"Playwright Chromium is unavailable: {error}")
            page = browser.new_page()
            for _ in range(30):
                try:
                    page.goto(url, wait_until="networkidle", timeout=5_000)
                    break
                except Exception:
                    time.sleep(1)
            else:
                pytest.fail("The Streamlit application did not become reachable.")
            yield page
            browser.close()
    finally:
        _compose("down", "--volumes", "--remove-orphans")


def test_complete_generation_edit_export_and_query_workflow(browser_page) -> None:  # type: ignore[no-untyped-def]
    page = browser_page
    page.locator('input[type="file"]').set_input_files(str(FIXTURE))
    page.get_by_label("Generation instructions").fill("Use realistic library data.")
    page.get_by_label("authors rows").fill("3")
    page.get_by_label("publishers rows").fill("3")
    page.get_by_label("books rows").fill("3")
    page.get_by_role("button", name="Generate").click()
    page.get_by_text("active version 1", exact=False).wait_for(timeout=30_000)

    page.get_by_label("Requested change").fill("Prefix authors for verification.")
    page.get_by_label("Requested change").press("Tab")
    page.get_by_role("button", name="Propose edit").click()
    page.get_by_text("Review the validated proposal", exact=False).wait_for()
    page.get_by_role("button", name="Confirm and create new version").click()
    page.get_by_text("version 2", exact=False).first.wait_for(timeout=30_000)

    with page.expect_download() as download_info:
        page.get_by_role("button", name="Download all tables (.zip)").click()
    assert download_info.value.suggested_filename.endswith(".zip")
    with page.expect_download() as download_info:
        page.get_by_role("button", name="Download authors CSV").click()
    assert download_info.value.suggested_filename == "authors.csv"

    page.get_by_text("Talk to your data", exact=True).last.click()
    page.get_by_label("Question").fill("Show the generated authors.")
    page.get_by_label("Question").press("Tab")
    page.get_by_role("button", name="Ask").click()
    page.get_by_text("Validated SQL", exact=True).wait_for(timeout=30_000)
    page.get_by_text("Deterministic browser-test query result.", exact=True).wait_for()
