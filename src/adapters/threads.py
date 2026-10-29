"""src/adapters/threads.py — V2 wrapper around the existing Threads client.

The live implementation in `src/publish/threads.py` works and is preserved.
This adapter is the seam V2 code talks to, so:
  * business logic never imports `requests` or reads THREADS_ACCESS_TOKEN
  * tests inject a fake and nothing reaches the network
  * dry_run short-circuits before any HTTP call

The adapter deliberately does NOT retry a publish. A timeout after submit is
ambiguous, and republishing is how a live account ends up with the same post
twice. That decision lives in orchestration/publisher.py.
"""
from __future__ import annotations

from typing import Optional

from src.core.config import get_settings, redact_secrets
from src.core.clock import utc_iso
from src.logger import get_logger

log = get_logger("adapters_threads")


class ThreadsAdapter:
    """Interface for every Threads side effect."""

    def publish_text(self, text: str, image_url: str = "") -> dict:
        raise NotImplementedError

    def publish_chain(self, parts: list[str], image_url: str = "") -> dict:
        raise NotImplementedError

    def lookup_post(self, post_id: str) -> dict:
        raise NotImplementedError

    def get_insights(self, post_id: str) -> dict:
        raise NotImplementedError


class DryRunThreadsAdapter(ThreadsAdapter):
    """No side effects whatsoever. The default for every offline path.

    A dry run reports a *synthetic* post id rather than an empty one. The
    publisher treats a missing post id as a failure — correctly, because for a
    live adapter it means the API gave us nothing. Returning "" here made every
    dry-run publish fail, which broke the offline rehearsal path entirely: the
    state machine could never reach PUBLISHED without touching the network.

    The synthetic id is namespaced `dryrun_` so it can never be confused with a
    real Threads id downstream, and `dry_run: True` marks the result for any
    caller that cares.
    """

    def __init__(self):
        self.calls: list[dict] = []
        self._counter = 0

    def _synthetic_id(self, kind: str) -> str:
        self._counter += 1
        return f"dryrun_{kind}_{self._counter}"

    def publish_text(self, text: str, image_url: str = "") -> dict:
        self.calls.append({"op": "publish_text", "text": text,
                           "image_url": image_url, "at": utc_iso()})
        log.info("[DRY RUN] publish_text suppressed: %s",
                 redact_secrets(text[:60]))
        return {"dry_run": True, "post_id": self._synthetic_id("text")}

    def publish_chain(self, parts: list[str], image_url: str = "") -> dict:
        self.calls.append({"op": "publish_chain", "parts": parts,
                           "image_url": image_url, "at": utc_iso()})
        log.info("[DRY RUN] publish_chain suppressed (%d parts)", len(parts))
        return {"dry_run": True, "post_id": self._synthetic_id("chain")}

    def lookup_post(self, post_id: str) -> dict:
        self.calls.append({"op": "lookup_post", "post_id": post_id})
        return {"found": False, "definitely_absent": False,
                "note": "dry run — no API call made"}

    def get_insights(self, post_id: str) -> dict:
        self.calls.append({"op": "get_insights", "post_id": post_id})
        return {"dry_run": True, "views": None, "likes": None, "replies": None}


class LiveThreadsAdapter(ThreadsAdapter):
    """Real publishing. Only constructed when live flags are on."""

    def __init__(self):
        cfg = get_settings().threads
        if not cfg.configured:
            raise RuntimeError(
                "LiveThreadsAdapter requires THREADS_USER_ID and "
                "THREADS_ACCESS_TOKEN"
            )
        self._cfg = cfg

    def publish_text(self, text: str, image_url: str = "") -> dict:
        from src.publish.threads import _create_container, _publish_container

        container = _create_container(text, image_url=image_url or None)
        post_id = _publish_container(container)
        return {"post_id": post_id, "container_id": container}

    def publish_chain(self, parts: list[str], image_url: str = "") -> dict:
        from src.publish.threads import post_thread_chain

        return post_thread_chain(parts, image_url=image_url or None)

    def lookup_post(self, post_id: str) -> dict:
        import requests

        try:
            r = requests.get(
                f"{self._cfg.base_url}/{post_id}",
                params={"fields": "id,text", "access_token": self._cfg.access_token},
                timeout=10,
            )
        except Exception as exc:  # noqa: BLE001
            return {"found": False, "definitely_absent": False,
                    "error": str(exc)}
        if r.status_code == 200:
            return {"found": True, "post_id": post_id,
                    "definitely_absent": False}
        if r.status_code == 404:
            return {"found": False, "post_id": post_id,
                    "definitely_absent": True}
        return {"found": False, "definitely_absent": False,
                "http_status": r.status_code}

    def get_insights(self, post_id: str) -> dict:
        import requests

        try:
            r = requests.get(
                f"{self._cfg.base_url}/{post_id}/insights",
                params={
                    "metric": "views,likes,replies",
                    "access_token": self._cfg.access_token,
                },
                timeout=15,
            )
            r.raise_for_status()
            data = r.json().get("data", [])
            out = {"views": None, "likes": None, "replies": None}
            for item in data:
                name = item.get("name")
                values = item.get("values") or [{}]
                if name in out and values:
                    out[name] = values[0].get("value")
            return out
        except Exception as exc:  # noqa: BLE001
            return {"error": redact_secrets(str(exc))}


def get_threads_adapter() -> ThreadsAdapter:
    """Dry run unless BOTH autopublish is on and dry_run is off."""
    flags = get_settings().flags
    if flags.live_publishing_allowed:
        return LiveThreadsAdapter()
    return DryRunThreadsAdapter()
