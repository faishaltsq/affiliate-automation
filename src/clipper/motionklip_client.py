"""motionklip_client.py — Discord REST API client for Motionklip clipping campaigns."""

import time
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
            keywords = ["campaign", "brief", "active", "info", "sayembara", "lomba", "clip"]
            for ch in channels_data:
                ch_name = ch.get("name", "").lower()
                ch_type = ch.get("type", 0)
                # type 0: guild text, type 15: forum channel, type 5: announcement
                if ch_type in (0, 5, 15) and any(k in ch_name for k in keywords):
                    discovered.append(str(ch["id"]))

            logger.info("Auto-discovered %d Motionklip campaign channels: %s", len(discovered), discovered)
            return discovered
        except Exception as e:
            logger.warning("Error discovering guild channels: %s", e)
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
            url = f"{self.base_url}/channels/{ch_id}/messages/{msg_id}"
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

                raw_msg = resp.json()
                if not isinstance(raw_msg, dict):
                    continue

                if not raw_msg.get("channel_id"):
                    raw_msg["channel_id"] = str(ch_id)

                detail = parse_discord_campaign_message(raw_msg)
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
