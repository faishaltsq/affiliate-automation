"""youtube_uploader.py — Upload video clips to YouTube Shorts via YouTube Data API v3."""

import json
import logging
import os
from pathlib import Path
from typing import Optional

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

logger = logging.getLogger(__name__)

SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly",
]

DEFAULT_CLIENT_SECRET = Path(__file__).resolve().parent.parent.parent / "data" / "tiktok" / "youtube_client_secret.json"
DEFAULT_TOKEN_FILE = Path(__file__).resolve().parent.parent.parent / "data" / "tiktok" / "youtube_oauth.json"


def get_authenticated_service(
    client_secret_file: Path = DEFAULT_CLIENT_SECRET,
    token_file: Path = DEFAULT_TOKEN_FILE,
):
    """Authenticate with YouTube Data API v3 and return YouTube service."""
    creds = None

    if token_file.exists():
        try:
            creds = Credentials.from_authorized_user_file(str(token_file), SCOPES)
        except Exception as e:
            logger.warning("Failed to load existing OAuth token: %s", e)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except Exception as e:
                logger.warning("Token refresh failed: %s, re-authenticating...", e)
                creds = None

        if not creds:
            if not client_secret_file.exists():
                raise FileNotFoundError(
                    f"Client secret file not found: {client_secret_file}. "
                    "Download it from Google Cloud Console."
                )

            flow = InstalledAppFlow.from_client_secrets_file(
                str(client_secret_file), SCOPES
            )
            # Use local server on free port to complete OAuth in default browser
            creds = flow.run_local_server(port=0, prompt="consent")

        # Save credentials for future runs
        token_file.parent.mkdir(parents=True, exist_ok=True)
        with open(token_file, "w", encoding="utf-8") as f:
            f.write(creds.to_json())

    return build("youtube", "v3", credentials=creds)


def upload_shorts(
    video_path: str,
    title: str,
    description: Optional[str] = None,
    tags: Optional[list[str]] = None,
    privacy_status: str = "public",
) -> dict:
    """Upload a video file to YouTube as a Short.

    Parameters:
        video_path: Path to .mp4 file (should be 9:16 portrait, <= 3 minutes)
        title: Video title (will include #Shorts if not already present)
        description: Video description text
        tags: List of tag strings
        privacy_status: 'public', 'unlisted', or 'private'

    Returns dict with {"success": bool, "video_id": str, "url": str, "error": str}
    """
    path = Path(video_path)
    if not path.exists():
        return {"success": False, "error": f"File not found: {video_path}"}

    # Ensure #Shorts tag in title or description to guarantee YouTube recognizes it as Short
    if "#Shorts" not in title and "#shorts" not in title:
        title_with_tag = f"{title[:90]} #Shorts"
    else:
        title_with_tag = title[:100]

    desc = description or f"{title}\n\n#Shorts #Viral #Satisfying"
    if "#Shorts" not in desc and "#shorts" not in desc:
        desc += "\n#Shorts"

    default_tags = ["Shorts", "Viral", "Satisfying", "TikTok", "Short"]
    if tags:
        default_tags = list(set(default_tags + tags))

    body = {
        "snippet": {
            "title": title_with_tag,
            "description": desc,
            "tags": default_tags,
            "categoryId": "22",  # People & Blogs
        },
        "status": {
            "privacyStatus": privacy_status,
            "selfDeclaredMadeForKids": False,
        },
    }

    try:
        youtube = get_authenticated_service()
        media = MediaFileUpload(
            str(path),
            mimetype="video/mp4",
            chunksize=-1,
            resumable=True,
        )

        request = youtube.videos().insert(
            part="snippet,status",
            body=body,
            media_body=media,
        )

        response = None
        while response is None:
            status, response = request.next_chunk()
            if status:
                logger.info("YouTube upload progress: %d%%", int(status.progress() * 100))

        video_id = response.get("id")
        url = f"https://youtube.com/shorts/{video_id}"
        logger.info("Successfully uploaded YouTube Short: %s", url)
        return {
            "success": True,
            "video_id": video_id,
            "url": url,
            "title": title_with_tag,
        }

    except Exception as exc:
        err = f"YouTube upload failed: {exc}"
        logger.error(err, exc_info=True)
        return {"success": False, "error": str(exc)}
