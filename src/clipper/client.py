"""client.py — Authenticated Clippo.id client via Playwright session."""

import os
import json
import logging
from pathlib import Path
from typing import Optional, Any
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

    def _clean_quill_ops(self, field_data: Any) -> str:
        """Extract plain text from Quill delta format {'ops': [{'insert': '...'}]}."""
        if not field_data:
            return ""
        if isinstance(field_data, str):
            return field_data.strip()
        if isinstance(field_data, dict) and "ops" in field_data:
            texts = []
            for op in field_data.get("ops", []):
                insert = op.get("insert", "")
                if isinstance(insert, str):
                    texts.append(insert)
            return "".join(texts).strip()
        return str(field_data)

    def get_campaigns(self, limit: int = 15, page: int = 1) -> list[dict]:
        """Fetch list of active campaigns with structured CPM, budget, and tags."""
        cookies = self._load_cookies()
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            ctx = browser.new_context()
            if cookies:
                ctx.add_cookies(cookies)
            page_obj = ctx.new_page()
            page_obj.goto("https://app.clippo.id/campaigns", wait_until="networkidle", timeout=30000)
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
                    "budget_percentage": float(item.get("budgetPercentage", 100)),
                    "hashtags": item.get("hashtags", []),
                    "materials_count": len(item.get("assetVideoUrlsV2", [])),
                    "start_date": item.get("startDate"),
                    "end_date": item.get("endDate"),
                })
            return results

    def get_campaign_detail(self, campaign_id: str) -> dict:
        """Fetch full campaign detail with all requirements, do's, don'ts, hooks, and video footage."""
        cookies = self._load_cookies()
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            ctx = browser.new_context()
            if cookies:
                ctx.add_cookies(cookies)
            page_obj = ctx.new_page()
            page_obj.goto("https://app.clippo.id/campaigns", wait_until="networkidle", timeout=30000)
            res = page_obj.evaluate(f"""async () => {{
                const r = await fetch('https://app.clippo.id/api/proxy/campaigns/{campaign_id}');
                return await r.json();
            }}""")
            browser.close()

            data = res.get("data", {})
            if not data:
                return {}

            # Parse assets / footage
            assets = []
            for a in data.get("assetVideoUrlsV2", []):
                if isinstance(a, dict):
                    assets.append({
                        "url": a.get("url"),
                        "display_name": a.get("displayText") or "",
                        "is_required": a.get("isRequired", False),
                    })
                elif isinstance(a, str):
                    assets.append({"url": a, "display_name": "", "is_required": False})

            # Additional materials from guidelines if any
            for g in data.get("guidelines", []):
                if isinstance(g, str) and (g.startswith("http") or "drive.google" in g):
                    if not any(x["url"] == g for x in assets):
                        assets.append({"url": g, "display_name": "Guideline Asset", "is_required": False})

            return {
                "id": data.get("id"),
                "title": data.get("title"),
                "cpm": data.get("ratePerKView", 0),
                "budget_percentage": float(data.get("budgetPercentage", 100)),
                "min_duration": data.get("minDuration", 15),
                "max_duration": data.get("maxDuration", 180),
                "hashtags": data.get("hashtags", []),
                "dos": data.get("campaignDos") or [],
                "donts": data.get("campaignDonts") or [],
                "hooks": self._clean_quill_ops(data.get("campaignHook")),
                "main_focus": self._clean_quill_ops(data.get("contentMainFocus")),
                "persona": self._clean_quill_ops(data.get("contentPersona")),
                "purpose": self._clean_quill_ops(data.get("campaignPurpose")),
                "cta": data.get("ctaLabel") or "",
                "assets": assets,
                "raw": data,
            }

    def get_session_user(self) -> dict:
        """Fetch current authenticated Clippo user."""
        cookies = self._load_cookies()
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            ctx = browser.new_context()
            if cookies:
                ctx.add_cookies(cookies)
            page_obj = ctx.new_page()
            page_obj.goto("https://app.clippo.id/campaigns", wait_until="networkidle", timeout=30000)
            res = page_obj.evaluate("""async () => {
                try {
                    const r = await fetch('https://app.clippo.id/api/auth/session');
                    return await r.json();
                } catch(e) {
                    return { error: e.message };
                }
            }""")
            browser.close()
            return res.get("user", {})
