# Clipper Automation (Clippo & Creator Highlights) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build an autonomous clipper pipeline in a dedicated Telegram topic (Thread 246) that scrapes active campaigns from `app.clippo.id` using authenticated cookies, analyzes campaign briefs and rules via LLM, downloads video assets (Google Drive/YouTube/TikTok), edits highlight clips into 9:16 short-form, dual-uploads to TikTok and YouTube Shorts, and reports submission links back to Telegram.

**Architecture:** A modular `src/clipper/` package separated from the existing satisfying video pipeline (`src/tiktok/`). `TelegramListener` routes messages in topic 246 to `src.bot.clipper_agent`. The agent communicates with `src.clipper.client` (Playwright session to Clippo internal proxy API), `src.clipper.brief_analyzer` (9router LLM), and `src.clipper.pipeline` (download, cut, render, and dual-upload via existing Playwright TikTok + YouTube Data API uploaders).

**Tech Stack:** Python 3.12+, Playwright (Chromium session with cookies), yt-dlp, FFmpeg, Antigravity 9router (`ag/gemini-3.8-flash-high`), YouTube Data API v3, Telegram Bot API.

**Spec:** User request to automate clipping from `https://app.clippo.id/campaigns` in a separate Telegram thread (`#246`), retaining dual-upload to TikTok and YouTube Shorts.

---

## Global Constraints

- Execution Path: `C:/Users/cubeb/OneDrive/Documents/coding/projects/affiliate-automation`
- Dedicated Telegram Topic: Thread ID `246` ("✂️ Clipper Automate (Clippo & Creators)")
- Existing Chat ID: `-1003757350269`
- Privacy & Secrets: `data/clippo/cookies.txt` must NEVER be tracked in Git (already added to `.gitignore`).
- Dual-Upload: Every processed clip must be uploaded to TikTok (`@pochitalkshop`) and YouTube Shorts (`pochitalk`).
- Model: `ag/gemini-3.8-flash-high` at `http://localhost:20128/v1` with `stream: False`.

## Review Focus

1. **Clippo Session Expiry:** Handling 401/403 or invalid cookies gracefully and falling back to unauthenticated public campaign listings.
2. **Google Drive Asset Access:** Extracting direct downloadable file URLs from Google Drive folders provided in Clippo briefs.
3. **Brief Compliance Verification:** Ensuring mandatory hashtags, tagged accounts, and required product mentions are included in the upload caption.
4. **Shorts Duration Cap:** Enforcing clip duration within 15–90 seconds (standard short-form CPM eligibility).
5. **Topic Separation:** Strict message isolation — thread 246 only handles clipper tasks; thread 83 handles satisfying timelapses; thread 9 handles Threads affiliate.

---

## Task Structure

### Task 1: Clippo API Client (`src/clipper/client.py`)

**Files:**
- Create: `src/clipper/client.py`
- Create: `tests/clipper/test_client.py`

**Interfaces:**
- Produces: `ClippoClient(cookies_path: str)` with methods `get_campaigns(limit=15, page=1) -> list[dict]`, `get_campaign_detail(campaign_id: str) -> dict`, `get_session_user() -> dict`.

- [ ] **Step 1: Write the failing test**

```python
# tests/clipper/test_client.py
import pytest
from unittest.mock import MagicMock, patch
from src.clipper.client import ClippoClient

def test_clippo_client_parse_campaigns():
    mock_payload = {
        "status": 200,
        "data": {
            "data": [
                {
                    "id": "cmv56bule000jep26bgduxjok",
                    "title": "BRIGHTY X FUJI X ACID",
                    "ratePerKView": 4000,
                    "budgetPercentage": 100,
                    "hashtags": ["#BrightySabunJumbo"],
                    "assetVideoUrlsV2": [{"url": "https://drive.google.com/test"}],
                }
            ]
        }
    }
    with patch("src.clipper.client.sync_playwright") as mock_pw:
        mock_browser = MagicMock()
        mock_ctx = MagicMock()
        mock_page = MagicMock()
        mock_page.evaluate.return_value = mock_payload
        mock_ctx.new_page.return_value = mock_page
        mock_browser.new_context.return_value = mock_ctx
        mock_pw.return_value.__enter__.return_value.chromium.launch.return_value = mock_browser

        client = ClippoClient()
        campaigns = client.get_campaigns(limit=5)
        assert len(campaigns) == 1
        assert campaigns[0]["id"] == "cmv56bule000jep26bgduxjok"
        assert campaigns[0]["cpm"] == 4000
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/clipper/test_client.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.clipper'`

- [ ] **Step 3: Implement `src/clipper/client.py`**

```python
"""client.py — Authenticated Clippo.id internal proxy client via Playwright."""
import os
import json
import logging
from pathlib import Path
from typing import Optional
from playwright.sync_api import sync_playwright

logger = logging.getLogger("clipper.client")

class ClippoClient:
    def __init__(self, cookies_path: Optional[str] = None):
        self.cookies_path = cookies_path or "data/clippo/cookies.txt"

    def _load_cookies(self) -> list[dict]:
        cookies = []
        if not os.path.exists(self.cookies_path):
            return cookies
        with open(self.cookies_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                parts = line.split("\t")
                if len(parts) >= 7:
                    cookies.append({
                        "name": parts[5],
                        "value": parts[6],
                        "domain": parts[0],
                        "path": parts[2],
                        "secure": parts[3].upper() == "TRUE",
                        "httpOnly": parts[1].upper() == "TRUE",
                    })
        return cookies

    def get_campaigns(self, limit: int = 15, page: int = 1) -> list[dict]:
        """Fetch list of active campaigns."""
        cookies = self._load_cookies()
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            ctx = browser.new_context()
            if cookies:
                ctx.add_cookies(cookies)
            page_obj = ctx.new_page()
            page_obj.goto("https://app.clippo.id/campaigns", wait_until="networkidle", timeout=25000)
            res = page_obj.evaluate(f"""async () => {{
                const r = await fetch('https://app.clippo.id/api/proxy/campaigns?limit={limit}&page={page}&orderBy=recommended');
                return await r.json();
            }}""")
            browser.close()

            items = res.get("data", {}).get("data", [])
            results = []
            for item in items:
                results.append({
                    "id": item.get("id"),
                    "title": item.get("title"),
                    "cpm": item.get("ratePerKView", 0),
                    "budget_percentage": item.get("budgetPercentage", 100),
                    "hashtags": item.get("hashtags", []),
                    "assets": item.get("assetVideoUrlsV2", []),
                    "guidelines": item.get("guidelines", []),
                    "raw": item,
                })
            return results

    def get_campaign_detail(self, campaign_id: str) -> dict:
        """Fetch detail and full guidelines of a specific campaign."""
        cookies = self._load_cookies()
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            ctx = browser.new_context()
            if cookies:
                ctx.add_cookies(cookies)
            page_obj = ctx.new_page()
            page_obj.goto("https://app.clippo.id/campaigns", wait_until="networkidle", timeout=25000)
            res = page_obj.evaluate(f"""async () => {{
                const r = await fetch('https://app.clippo.id/api/proxy/campaigns/{campaign_id}');
                return await r.json();
            }}""")
            browser.close()
            return res.get("data", {})
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/clipper/test_client.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/clipper/client.py tests/clipper/test_client.py
git commit -m "feat(clipper): add authenticated Clippo API client"
```

---

### Task 2: AI Brief Analyzer (`src/clipper/brief_analyzer.py`)

**Files:**
- Create: `src/clipper/brief_analyzer.py`
- Create: `tests/clipper/test_brief_analyzer.py`

**Interfaces:**
- Consumes: Campaign dict from `ClippoClient`.
- Produces: `analyze_brief(campaign: dict) -> dict` returning:
  `{"hooks": list[str], "dos": list[str], "donts": list[str], "hashtags": list[str], "mentions": list[str], "materials": list[str], "summary": str}`

- [ ] **Step 1: Write the failing test**

```python
# tests/clipper/test_brief_analyzer.py
from unittest.mock import patch, MagicMock
from src.clipper.brief_analyzer import analyze_brief

def test_analyze_brief_extracts_hashtags_and_materials():
    campaign = {
        "title": "BRIGHTY X FUJI",
        "ratePerKView": 4000,
        "hashtags": ["#BrightySabunJumbo", "#brighty"],
        "assetVideoUrlsV2": [{"url": "https://drive.google.com/123"}],
        "requirementsV2": "<p>Wajib bahas produk, jangan cut saat produk muncul</p>",
    }
    with patch("src.clipper.brief_analyzer.requests.post") as mock_post:
        mock_resp = MagicMock()
        mock_resp.ok = True
        mock_resp.json.return_value = {
            "choices": [{
                "message": {
                    "content": '{"hooks": ["Gede banget sabunnya!"], "dos": ["Bahas produk"], "donts": ["Jangan cut produk"], "summary": "Promosi sabun jumbo Brighty"}'
                }
            }]
        }
        mock_post.return_value = mock_resp
        result = analyze_brief(campaign)
        assert len(result["hashtags"]) == 2
        assert result["materials"][0] == "https://drive.google.com/123"
        assert "Gede banget sabunnya!" in result["hooks"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/clipper/test_brief_analyzer.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement `src/clipper/brief_analyzer.py`**

```python
"""brief_analyzer.py — Extract actionable clipping rules & winning hooks via LLM."""
import os
import re
import json
import logging
import requests

logger = logging.getLogger("clipper.brief_analyzer")
LLM_URL = os.environ.get("OPENROUTER_BASE_URL", "http://localhost:20128/v1/chat/completions")
MODEL = os.environ.get("OPENROUTER_MODEL", "ag/gemini-3.8-flash-high")

def _clean_html(raw_html: str) -> str:
    return re.sub(r"<[^>]+>", " ", raw_html or "").strip()

def analyze_brief(campaign: dict) -> dict:
    title = campaign.get("title", "")
    hashtags = campaign.get("hashtags", [])
    raw_req = campaign.get("requirementsV2") or campaign.get("requirements") or ""
    clean_req = _clean_html(raw_req)

    # Extract all asset URLs
    materials = []
    for item in campaign.get("assetVideoUrlsV2", []):
        url = item.get("url") if isinstance(item, dict) else item
        if url and url not in materials:
            materials.append(url)
    for g in campaign.get("guidelines", []):
        if isinstance(g, str) and (g.startswith("http") or "drive.google" in g) and g not in materials:
            materials.append(g)

    # Prompt LLM for structured analysis
    system_prompt = (
        "Kamu adalah Senior Content Strategist & Video Clipper expert TikTok & Shorts. "
        "Analisis brief campaign berikut dan hasilkan JSON valid dengan format:\n"
        "{\n"
        '  "summary": "Ringkasan pesan utama campaign dalam 1 kalimat",\n'
        '  "hooks": ["3 ide hook pembuka video yang bikin orang berhenti scroll"],\n'
        '  "dos": ["Poin-poin yang wajib ada di video"],\n'
        '  "donts": ["Poin-poin yang dilarang/diharamkan di video"]\n'
        "}\n"
        "Hanya outputkan JSON valid tanpa markdown formatting."
    )
    user_prompt = f"Judul: {title}\nAturan & Deskripsi:\n{clean_req}"

    hooks, dos, donts, summary = [], [], [], ""
    try:
        r = requests.post(
            LLM_URL,
            json={
                "model": MODEL,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                "stream": False,
                "temperature": 0.4,
            },
            timeout=15,
        )
        if r.ok:
            content = r.json()["choices"][0]["message"]["content"].strip()
            # Clean markdown code block if present
            if content.startswith("```"):
                content = re.sub(r"^```(?:json)?\s*", "", content)
                content = re.sub(r"\s*```$", "", content)
            parsed = json.loads(content)
            hooks = parsed.get("hooks", [])
            dos = parsed.get("dos", [])
            donts = parsed.get("donts", [])
            summary = parsed.get("summary", "")
    except Exception as e:
        logger.warning("LLM brief analysis fallback: %s", e)
        summary = f"Campaign {title}"
        hooks = [f"Kalian harus lihat ini! {title}"]

    return {
        "campaign_id": campaign.get("id"),
        "title": title,
        "cpm": campaign.get("ratePerKView", 0),
        "hashtags": hashtags,
        "materials": materials,
        "summary": summary,
        "hooks": hooks,
        "dos": dos,
        "donts": donts,
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/clipper/test_brief_analyzer.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/clipper/brief_analyzer.py tests/clipper/test_brief_analyzer.py
git commit -m "feat(clipper): add AI brief analyzer for Clippo campaigns"
```

---

### Task 3: Clipper Pipeline (`src/clipper/pipeline.py`)

**Files:**
- Create: `src/clipper/pipeline.py`
- Modify: `src/tiktok/youtube_uploader.py` (reusable by clipper)
- Modify: `src/tiktok/uploader.py` (reusable by clipper)

**Interfaces:**
- Consumes: `ClippoClient`, `analyze_brief`, `upload_shorts`, `TikTokUploader`.
- Produces: `run_clipper_pipeline(campaign_id: str, source_url: Optional[str], progress_callback: Optional[Callable]) -> dict`

- [ ] **Step 1: Write the failing test**

```python
# tests/clipper/test_pipeline.py
from unittest.mock import patch, MagicMock
from src.clipper.pipeline import run_clipper_pipeline

def test_run_clipper_pipeline_dry_run():
    with patch("src.clipper.pipeline.ClippoClient") as mock_client:
        mock_instance = MagicMock()
        mock_instance.get_campaign_detail.return_value = {
            "id": "test_123",
            "title": "Test Campaign",
            "ratePerKView": 3000,
            "hashtags": ["#test"],
            "assetVideoUrlsV2": [],
        }
        mock_client.return_value = mock_instance
        res = run_clipper_pipeline(campaign_id="test_123", dry_run=True)
        assert res["campaign_id"] == "test_123"
        assert res["title"] == "Test Campaign"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/clipper/test_pipeline.py -v`
Expected: FAIL

- [ ] **Step 3: Implement `src/clipper/pipeline.py`**

Build the end-to-end clipper orchestrator:
1. Fetch campaign brief via `ClippoClient`.
2. Extract rules and hashtags via `analyze_brief`.
3. Download footage (from provided URL or campaign assets).
4. Render 9:16 vertical clip with hook subtitle overlay.
5. Dual-upload to TikTok + YouTube Shorts.
6. Assemble ready-to-submit link metadata.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/clipper/test_pipeline.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/clipper/pipeline.py tests/clipper/test_pipeline.py
git commit -m "feat(clipper): add end-to-end clipper execution pipeline"
```

---

### Task 4: Clipper Telegram Agent (`src/bot/clipper_agent.py`)

**Files:**
- Create: `src/bot/clipper_agent.py`
- Modify: `src/bot/telegram_listener.py` (route thread `246` to `clipper_agent`)
- Create: `tests/bot/test_clipper_agent.py`

**Interfaces:**
- Consumes: Telegram updates where `message_thread_id == 246`.
- Produces: Commands:
  - `/campaigns` : List top Clippo campaigns by CPM
  - `/brief <id>` : Show detailed AI brief & hooks
  - `/clip <id> [url]` : Execute clipping and dual-upload
  - Natural conversation: conversational AI assistant answering clipping strategies and advice.

- [ ] **Step 1: Write the failing test**

```python
# tests/bot/test_clipper_agent.py
from unittest.mock import patch, MagicMock
from src.bot.clipper_agent import handle_clipper_message

def test_clipper_agent_handles_campaigns_cmd():
    with patch("src.bot.clipper_agent._send_reply") as mock_send, \
         patch("src.bot.clipper_agent.ClippoClient") as mock_cli:
        mock_inst = MagicMock()
        mock_inst.get_campaigns.return_value = [
            {"id": "c1", "title": "Brighty", "cpm": 4000, "budget_percentage": 100}
        ]
        mock_cli.return_value = mock_inst
        handle_clipper_message({}, "/campaigns", msg_id=1, thread_id=246)
        assert mock_send.called
        assert "Brighty" in mock_send.call_args[0][0]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/bot/test_clipper_agent.py -v`
Expected: FAIL

- [ ] **Step 3: Implement `src/bot/clipper_agent.py` and route in `telegram_listener.py`**

Update `src/bot/telegram_listener.py`:
```python
CLIPPER_THREAD_ID = int(os.environ.get("TG_CLIPPER_THREAD_ID", "246"))

# inside handle_message:
if raw_thread_id == CLIPPER_THREAD_ID:
    handle_clipper_message(msg, text, msg_id, CLIPPER_THREAD_ID)
elif raw_thread_id == int(TIKTOK_THREAD_ID):
    handle_tiktok_message(msg, text, msg_id, int(TIKTOK_THREAD_ID))
else:
    handle_threads_message(msg, text, msg_id, raw_thread_id)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/bot/test_clipper_agent.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/bot/clipper_agent.py src/bot/telegram_listener.py tests/bot/test_clipper_agent.py
git commit -m "feat(clipper): add Telegram agent for Thread 246 with Clippo routing"
```

---

### Task 5: End-to-End Verification & Telegram Startup Card

**Files:**
- Modify: `main.py`
- Modify: `data/tiktok/config.json`

- [ ] **Step 1: Run full test suite**
Run: `pytest tests/ -v`
Expected: All tests pass.

- [ ] **Step 2: Send startup welcome card to Topic 246**
Post welcoming interactive guide with `/campaigns` sample directly to Topic 246 on Telegram.

- [ ] **Step 3: Restart daemon & verify live process**
Restart `python main.py bot` and verify both Topic 83 (Satisfying), Topic 9 (Threads), and Topic 246 (Clipper) are active.
