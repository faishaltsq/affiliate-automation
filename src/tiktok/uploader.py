"""uploader.py — TikTok video upload wrapper using tiktok-uploader (Playwright)."""

import logging
import os
import subprocess
import sys

logger = logging.getLogger(__name__)


class TikTokUploader:
    """Wrapper around tiktok-uploader with error handling and subprocess isolation."""

    def __init__(self, cookies_path: str):
        self.cookies_path = os.path.abspath(cookies_path)
        if not os.path.isfile(self.cookies_path):
            raise FileNotFoundError(f"Cookies file not found: {self.cookies_path}")

    def upload_video(self, video_path: str, description: str) -> bool:
        """
        Upload a single video to TikTok in an isolated subprocess.
        Returns True on success, False on failure.
        """
        abs_video_path = os.path.abspath(video_path)
        if not os.path.isfile(abs_video_path):
            logger.error(f"Video file not found: {abs_video_path}")
            return False

        script = (
            "import sys, time, logging\n"
            "logging.basicConfig(level=logging.INFO)\n"
            "import tiktok_uploader.upload as upload_module\n"
            "from tiktok_uploader.upload import TikTokUploader\n"
            "\n"
            "original_remove_split = upload_module._remove_split_window\n"
            "original_post_video = upload_module._post_video\n"
            "\n"
            "def dismiss_popups(page):\n"
            "    try:\n"
            "        original_remove_split(page)\n"
            "    except Exception:\n"
            "        pass\n"
            "    try:\n"
            "        got_it = page.locator('button:has-text(\"Got it\"), div:has-text(\"Got it\")').all()\n"
            "        for b in got_it:\n"
            "            if b.is_visible():\n"
            "                b.click()\n"
            "                time.sleep(0.5)\n"
            "    except Exception:\n"
            "        pass\n"
            "    try:\n"
            "        page.evaluate('''() => {\n"
            "            document.querySelectorAll('.react-joyride__overlay, .react-joyride__tooltip, [role=\"alertdialog\"]').forEach(el => el.remove());\n"
            "        }''')\n"
            "    except Exception:\n"
            "        pass\n"
            "\n"
            "def patched_post_video(page):\n"
            "    dismiss_popups(page)\n"
            "    time.sleep(1)\n"
            "    return original_post_video(page)\n"
            "\n"
            "upload_module._remove_split_window = dismiss_popups\n"
            "upload_module._post_video = patched_post_video\n"
            "\n"
            "uploader = TikTokUploader(cookies=sys.argv[1])\n"
            "success = uploader.upload_video(sys.argv[2], description=sys.argv[3])\n"
            "sys.exit(0 if success else 1)\n"
        )

        try:
            cmd = [
                sys.executable,
                "-c",
                script,
                self.cookies_path,
                abs_video_path,
                description,
            ]
            logger.info("Executing upload via isolated subprocess...")
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=900)

            if proc.returncode == 0:
                logger.info(f"Subprocess upload succeeded: {abs_video_path}")
                return True
            else:
                logger.error(f"Subprocess upload failed (exit {proc.returncode}): {proc.stderr[-400:]}")
                return False
        except subprocess.TimeoutExpired:
            logger.error(f"Subprocess upload timed out for: {abs_video_path}")
            return False
        except Exception as e:
            logger.error(f"Upload execution error for {abs_video_path}: {e}")
            return False

    def upload_videos(self, videos: list[dict]) -> list[dict]:
        """Upload multiple videos. Return list of failed uploads."""
        failed = []
        for v in videos:
            if not self.upload_video(v["path"], v["description"]):
                failed.append(v)
        return failed
