"""motionklip_client.py — Discord REST API client for Motionklip clipping campaigns."""

import time
import re
import logging
from typing import Optional, Any
import requests

from src.config import DISCORD_TOKEN, MOTIONKLIP_GUILD_ID, MOTIONKLIP_CHANNELS
from src.clipper.motionklip_parser import parse_discord_campaign_message

logger = logging.getLogger("clipper.motionklip_client")


class MotionklipClient:
    """Discord REST client to fetch and parse campaign briefs from Motionklip Discord server."""

    def __init__(
        self,
        token: Optional[str] = None,
        channels: Optional[list[str]] = None,
        guild_id: Optional[str] = None,
        cache_ttl: int = 300,
        is_bot: bool = False,
    ):
        self.token = token if token is not None else DISCORD_TOKEN
        self.channels = list(channels) if channels is not None else list(MOTIONKLIP_CHANNELS)
        self.guild_id = guild_id if guild_id is not None else MOTIONKLIP_GUILD_ID
        self.cache_ttl = cache_ttl
        self.is_bot = is_bot
        self.base_url = "https://discord.com/api/v10"

        # In-memory caches: (data, timestamp)
        self._campaigns_cache: Optional[tuple[list[dict], float]] = None
        self._detail_cache: dict[str, tuple[dict, float]] = {}
        self._message_channel_map: dict[str, str] = {}

    def _get_headers(self) -> dict[str, str]:
        """Construct authorization and request headers for Discord API."""
        if not self.token:
            return {}

        auth_val = self.token
        if self.is_bot and not (auth_val.startswith("Bot ") or auth_val.startswith("Bearer ")):
            auth_val = f"Bot {auth_val}"

        return {
            "Authorization": auth_val,
            "Content-Type": "application/json",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        }

    def _discover_channels(self) -> list[str]:
        """Auto-discover campaign channels from guild if MOTIONKLIP_GUILD_ID is provided."""
        if not self.token or not self.guild_id:
            return []

        try:
            url = f"{self.base_url}/guilds/{self.guild_id}/channels"
            resp = requests.get(url, headers=self._get_headers(), timeout=10)
            if resp.status_code != 200:
                logger.warning("Failed to discover guild channels: status %d", resp.status_code)
                return []

            channels_data = resp.json()
            if not isinstance(channels_data, list):
                return []

            # Filter for channels matching campaign-related keywords
            discovered = []
            keywords = [
                "announcement", "campaign", "brief", "active", "info",
                "sayembara", "lomba", "clip", "bahan-dan-rules"
            ]
            for ch in channels_data:
                ch_name = ch.get("name", "").lower()
                ch_type = ch.get("type", 0)
                # type 0: guild text, type 15: forum channel, type 5: announcement
                if ch_type in (0, 5, 15) and any(k in ch_name for k in keywords):
                    discovered.append(str(ch["id"]))

            # Sort channels by snowflake ID descending (newest channels first)
            discovered.sort(key=lambda cid: int(cid) if cid.isdigit() else 0, reverse=True)
            logger.info("Auto-discovered %d Motionklip campaign channels", len(discovered))
            return discovered
        except Exception as e:
            logger.warning("Error discovering guild channels: %s", e)
            return []

    def _fetch_from_guild_categories(self, limit: int = 15) -> list[dict]:
        """Fetch structured campaign pairs (announcement + bahan) grouped by Discord category."""
        if not self.token or not self.guild_id:
            return []

        try:
            url = f"{self.base_url}/guilds/{self.guild_id}/channels"
            resp = requests.get(url, headers=self._get_headers(), timeout=12)
            if resp.status_code != 200:
                return []

            channels_data = resp.json()
            if not isinstance(channels_data, list):
                return []

            # Group channels by category (parent_id)
            categories: dict[str, list[dict]] = {}
            for ch in channels_data:
                pid = ch.get("parent_id")
                if pid:
                    categories.setdefault(str(pid), []).append(ch)

            campaign_cats = []
            for pid, sibs in categories.items():
                ann = next((c for c in sibs if "announcement" in c.get("name", "").lower()), None)
                bahan = next((c for c in sibs if "bahan" in c.get("name", "").lower()), None)
                budget_ch = next((c for c in sibs if "budget" in c.get("name", "").lower()), None)
                if not ann:
                    continue

                budget_pct = 100.0
                if budget_ch:
                    bm = re.search(r'(\d+(?:\.\d+)?)\s*%', budget_ch.get("name", ""))
                    if bm:
                        try:
                            budget_pct = float(bm.group(1))
                        except ValueError:
                            pass

                campaign_cats.append({
                    "parent_id": pid,
                    "ann_id": str(ann["id"]),
                    "ann_name": ann.get("name", ""),
                    "bahan_id": str(bahan["id"]) if bahan else None,
                    "budget_pct": budget_pct,
                })

            # Sort newest first
            campaign_cats.sort(
                key=lambda x: int(x["ann_id"]) if x["ann_id"].isdigit() else 0,
                reverse=True,
            )

            results: list[dict] = []
            headers = self._get_headers()

            for cat in campaign_cats[:limit]:
                try:
                    ann_url = f"{self.base_url}/channels/{cat['ann_id']}/messages?limit=1"
                    r_ann = requests.get(ann_url, headers=headers, timeout=10)
                    if not r_ann.ok:
                        continue
                    ann_list = r_ann.json()
                    if not isinstance(ann_list, list) or not ann_list:
                        continue

                    ann_msg = ann_list[0]
                    parsed = parse_discord_campaign_message(ann_msg)
                    parsed["budget_percentage"] = cat["budget_pct"]
                    parsed["channel_id"] = cat["ann_id"]

                    # If bahan channel exists, merge hashtags, rules, and footage
                    if cat["bahan_id"]:
                        self._message_channel_map[str(cat["bahan_id"])] = cat["bahan_id"]
                        b_url = f"{self.base_url}/channels/{cat['bahan_id']}/messages?limit=1"
                        r_b = requests.get(b_url, headers=headers, timeout=10)
                        if r_b.ok:
                            b_list = r_b.json()
                            if isinstance(b_list, list) and b_list:
                                b_msg = b_list[0]
                                b_parsed = parse_discord_campaign_message(b_msg)
                                if b_parsed.get("hashtags"):
                                    parsed["hashtags"] = b_parsed["hashtags"]
                                if b_parsed.get("assets"):
                                    for a in b_parsed["assets"]:
                                        if a not in parsed["assets"]:
                                            parsed["assets"].append(a)
                                if b_parsed.get("dos"):
                                    for d in b_parsed["dos"]:
                                        if d not in parsed["dos"]:
                                            parsed["dos"].append(d)
                                if b_parsed.get("donts"):
                                    for d in b_parsed["donts"]:
                                        if d not in parsed["donts"]:
                                            parsed["donts"].append(d)
                                # Map bahan message id as well
                                b_msg_id = str(b_msg.get("id", ""))
                                if b_msg_id:
                                    self._message_channel_map[b_msg_id] = cat["bahan_id"]

                    parsed["materials_count"] = len(parsed.get("assets", []))
                    results.append(parsed)

                    # Update detail cache
                    c_id = parsed["id"]
                    msg_id = str(ann_msg.get("id", ""))
                    self._detail_cache[c_id] = (parsed, time.time())
                    if msg_id:
                        self._message_channel_map[msg_id] = cat["ann_id"]

                except Exception as ex:
                    logger.warning("Error fetching category %s: %s", cat.get("ann_name"), ex)
                    continue

            return results
        except Exception as e:
            logger.warning("Error during category-based guild fetch: %s", e)
            return []

    def get_campaigns(self, limit: int = 15, force_refresh: bool = False) -> list[dict]:
        """Fetch active campaigns from Discord channels with caching and error fallback.

        Returns list of parsed campaign dictionaries prefixed with 'mk-'.
        """
        if not self.token:
            logger.warning("DISCORD_TOKEN is missing. Returning empty campaigns list.")
            return []

        # Check in-memory cache
        if not force_refresh and self._campaigns_cache is not None:
            cached_data, cached_time = self._campaigns_cache
            if time.time() - cached_time < self.cache_ttl:
                return cached_data

        target_channels = list(self.channels)
        if not target_channels and self.guild_id:
            # First attempt category-based grouped fetch (announcement + bahan + budget)
            cat_campaigns = self._fetch_from_guild_categories(limit=limit)
            if cat_campaigns:
                self._campaigns_cache = (cat_campaigns, time.time())
                return cat_campaigns
            target_channels = self._discover_channels()

        if not target_channels:
            logger.warning("No Motionklip channels configured or discovered.")
            return []

        all_campaigns: list[dict] = []
        headers = self._get_headers()

        for ch_id in target_channels:
            url = f"{self.base_url}/channels/{ch_id}/messages?limit={limit}"
            try:
                resp = requests.get(url, headers=headers, timeout=15)
                if resp.status_code == 429:
                    retry_after = resp.json().get("retry_after", 1) if resp.text else 1
                    logger.warning("Discord API rate limited on channel %s (retry_after=%s)", ch_id, retry_after)
                    continue
                elif resp.status_code != 200:
                    logger.warning("Discord API returned status %d for channel %s", resp.status_code, ch_id)
                    continue

                messages = resp.json()
                if not isinstance(messages, list):
                    continue

                for msg in messages:
                    if not isinstance(msg, dict):
                        continue
                    # Ensure channel_id is recorded
                    if not msg.get("channel_id"):
                        msg["channel_id"] = str(ch_id)

                    parsed = parse_discord_campaign_message(msg)
                    parsed["materials_count"] = len(parsed.get("assets", []))
                    all_campaigns.append(parsed)

                    # Update lookup caches
                    campaign_id = parsed["id"]
                    msg_id = str(msg.get("id", ""))
                    self._detail_cache[campaign_id] = (parsed, time.time())
                    if msg_id:
                        self._message_channel_map[msg_id] = str(ch_id)

            except requests.exceptions.RequestException as e:
                logger.error("Network error fetching messages for channel %s: %s", ch_id, e)
                continue
            except Exception as e:
                logger.error("Unexpected error processing channel %s messages: %s", ch_id, e)
                continue

        # Save to campaigns cache
        self._campaigns_cache = (all_campaigns, time.time())
        return all_campaigns

    def get_campaign_detail(self, campaign_id: str, force_refresh: bool = False) -> dict:
        """Fetch full campaign detail by campaign ID (e.g. 'mk-123' or '123').

        Uses cached details or queries Discord channel message endpoint directly.
        """
        if not self.token or not campaign_id:
            return {}

        norm_id = campaign_id if campaign_id.startswith("mk-") else f"mk-{campaign_id}"
        msg_id = norm_id[3:]

        # Check detail cache
        if not force_refresh and norm_id in self._detail_cache:
            cached_data, cached_time = self._detail_cache[norm_id]
            if time.time() - cached_time < self.cache_ttl:
                return cached_data

        target_channels = []
        if msg_id in self._message_channel_map:
            target_channels.append(self._message_channel_map[msg_id])
        target_channels.extend([c for c in self.channels if c not in target_channels])

        if not target_channels and self.guild_id:
            target_channels = self._discover_channels()

        if not target_channels:
            logger.warning("No channels available to fetch campaign detail for %s", campaign_id)
            return {}

        headers = self._get_headers()
        for ch_id in target_channels:
            # Note: Discord restricts /messages/{msg_id} to bot tokens only.
            # Using /messages?around={msg_id}&limit=1 works seamlessly for both bot and user tokens.
            url = f"{self.base_url}/channels/{ch_id}/messages?around={msg_id}&limit=1"
            try:
                resp = requests.get(url, headers=headers, timeout=15)
                if resp.status_code == 429:
                    retry_after = resp.json().get("retry_after", 1) if resp.text else 1
                    logger.warning("Discord API rate limited fetching message %s in channel %s (retry_after=%s)", msg_id, ch_id, retry_after)
                    return {}
                elif resp.status_code == 404:
                    # Message not in this channel, try next
                    continue
                elif resp.status_code != 200:
                    logger.warning("Discord API returned status %d fetching message %s in channel %s", resp.status_code, msg_id, ch_id)
                    continue

                res_data = resp.json()
                raw_msg = None
                if isinstance(res_data, list) and res_data:
                    # Find exact message by ID if returned in list
                    for m in res_data:
                        if str(m.get("id")) == str(msg_id):
                            raw_msg = m
                            break
                    if not raw_msg and res_data:
                        raw_msg = res_data[0]
                elif isinstance(res_data, dict):
                    raw_msg = res_data

                if not raw_msg or not isinstance(raw_msg, dict):
                    continue

                if not raw_msg.get("channel_id"):
                    raw_msg["channel_id"] = str(ch_id)

                detail = parse_discord_campaign_message(raw_msg)

                # If missing assets or hashtags, check sibling bahan channel in guild
                if (not detail.get("assets") or not detail.get("hashtags")) and self.guild_id and not self.channels:
                    try:
                        ch_info = requests.get(f"{self.base_url}/channels/{ch_id}", headers=headers, timeout=8).json()
                        parent_id = ch_info.get("parent_id")
                        if parent_id:
                            g_channels = requests.get(f"{self.base_url}/guilds/{self.guild_id}/channels", headers=headers, timeout=8).json()
                            if isinstance(g_channels, list):
                                bahan_ch = next((c for c in g_channels if c.get("parent_id") == parent_id and "bahan" in c.get("name", "").lower()), None)
                                budget_ch = next((c for c in g_channels if c.get("parent_id") == parent_id and "budget" in c.get("name", "").lower()), None)
                                if budget_ch and detail.get("budget_percentage", 100.0) == 100.0:
                                    bm = re.search(r'(\d+(?:\.\d+)?)\s*%', budget_ch.get("name", ""))
                                    if bm:
                                        detail["budget_percentage"] = float(bm.group(1))

                                if bahan_ch:
                                    b_resp = requests.get(f"{self.base_url}/channels/{bahan_ch['id']}/messages?limit=1", headers=headers, timeout=8)
                                    if b_resp.ok and b_resp.json():
                                        b_parsed = parse_discord_campaign_message(b_resp.json()[0])
                                        if b_parsed.get("hashtags") and not detail.get("hashtags"):
                                            detail["hashtags"] = b_parsed["hashtags"]
                                        if b_parsed.get("assets"):
                                            detail.setdefault("assets", [])
                                            for a in b_parsed["assets"]:
                                                if a not in detail["assets"]:
                                                    detail["assets"].append(a)
                                        if b_parsed.get("dos") and not detail.get("dos"):
                                            detail["dos"] = b_parsed["dos"]
                                        if b_parsed.get("donts") and not detail.get("donts"):
                                            detail["donts"] = b_parsed["donts"]
                    except Exception as e_sib:
                        logger.debug("Failed sibling channel lookup: %s", e_sib)

                detail["materials_count"] = len(detail.get("assets", []))

                # Cache detail
                self._detail_cache[norm_id] = (detail, time.time())
                self._message_channel_map[msg_id] = str(ch_id)
                return detail

            except requests.exceptions.RequestException as e:
                logger.error("Network error fetching campaign detail %s: %s", campaign_id, e)
                return {}
            except Exception as e:
                logger.error("Unexpected error fetching campaign detail %s: %s", campaign_id, e)
                return {}

        return {}
