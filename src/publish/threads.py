"""
Threads Publisher — posts text + optional image to Threads via API.
Strategy: post utama (vibe hook, no link) → reply (produk + link afiliasi)
"""

import os
import time
import requests
from dotenv import load_dotenv
from pathlib import Path
from src.logger import get_logger

load_dotenv(Path(__file__).parent.parent.parent / '.env', override=True)

log = get_logger("publish.threads")

USER_ID = os.getenv('THREADS_USER_ID')
ACCESS_TOKEN = os.getenv('THREADS_ACCESS_TOKEN')
BASE_URL = 'https://graph.threads.net/v1.0'


def _create_container(text: str, image_url: str = None, reply_to_id: str = None) -> str:
    """Create a media container, return container ID."""
    params = {
        'access_token': ACCESS_TOKEN,
        'text': text,
    }
    if image_url:
        params['media_type'] = 'IMAGE'
        params['image_url'] = image_url
    else:
        params['media_type'] = 'TEXT'

    if reply_to_id:
        params['reply_to_id'] = reply_to_id

    r = requests.post(f'{BASE_URL}/{USER_ID}/threads', params=params, timeout=30)
    r.raise_for_status()
    return r.json()['id']


def _publish_container(container_id: str) -> str:
    """Publish a container, return published post ID."""
    r = requests.post(
        f'{BASE_URL}/{USER_ID}/threads_publish',
        params={
            'creation_id': container_id,
            'access_token': ACCESS_TOKEN,
        },
        timeout=30,
    )
    r.raise_for_status()
    return r.json()['id']


def post_threads(hook_text: str, reply_text: str,
                 vibe_image_url: str = None,
                 product_image_url: str = None) -> dict:
    """
    Post to Threads:
    - Post 1: hook text + vibe image (no link)
    - Reply 1: reply text + product image (with affiliate link)

    Returns dict with post_id and reply_id.
    """
    results = {}

    try:
        # Post utama — vibe hook
        log.info('[Threads] Creating main post container...')
        main_container = _create_container(hook_text, image_url=vibe_image_url)
        time.sleep(2)  # Threads recommends waiting before publish

        log.info('[Threads] Publishing main post...')
        post_id = _publish_container(main_container)
        results['post_id'] = post_id
        log.info(f'[Threads] Main post published: {post_id}')

        time.sleep(3)  # Wait before reply

        # Reply — optional (skip if reply_text is None/empty)
        if reply_text:
            log.info('[Threads] Creating reply container...')
            reply_container = _create_container(
                reply_text,
                image_url=product_image_url,
                reply_to_id=post_id
            )
            time.sleep(2)

            log.info('[Threads] Publishing reply...')
            reply_id = _publish_container(reply_container)
            results['reply_id'] = reply_id
            log.info(f'[Threads] Reply published: {reply_id}')

    except requests.HTTPError as e:
        log.error(f'[Threads] HTTP Error: {e.response.status_code} {e.response.text}')
        results['error'] = e.response.text
    except Exception as e:
        log.error(f'[Threads] Error: {e}')
        results['error'] = str(e)

    return results


def _publish_text(text: str, image_url: str = None, reply_to_id: str = None) -> str:
    """Create + publish one post, waiting between the two Meta calls."""
    container = _create_container(
        text, image_url=image_url, reply_to_id=reply_to_id or None
    )
    time.sleep(2)
    return _publish_container(container)


def post_thread_chain(
    parts: list,
    image_url: str = None,
    bonus_reply: str = None,
    part_delay: int = 3,
) -> dict:
    """
    Publish a multi-part thread plus one optional bonus reply.

    Part 1 is the root post and carries the image. Every later part is published
    as a reply to the PREVIOUS part, not to the root: Threads ranks first-level
    replies by engagement, so siblings would get reshuffled mid-read, while a
    real chain always renders in order.

    The bonus reply is attached to the ROOT instead, so it lands in the reply
    section as a sibling — that is where readers arrive after finishing the
    chain, which is the whole point of holding a fragment back.

    Returns {post_id, part_ids, bonus_reply_id, error}. On a mid-chain failure the
    ids published so far are kept so a caller can resume rather than duplicate.
    """
    results = {"post_id": "", "part_ids": [], "bonus_reply_id": ""}

    clean_parts = [str(p).strip() for p in (parts or []) if str(p or "").strip()]
    if not clean_parts:
        results["error"] = "no publishable parts"
        return results

    root_id = ""
    parent_id = ""
    for idx, text in enumerate(clean_parts):
        is_root = idx == 0
        log.info(f"[Threads] Chain part {idx + 1}/{len(clean_parts)}"
                 f"{' (root, image)' if is_root and image_url else ''}...")

        try:
            new_id = _publish_text(text, image_url if is_root else None, parent_id)
        except Exception as e:
            if not is_root or not image_url:
                log.error(f"[Threads] Chain aborted at part {idx + 1}: {e}")
                results["error"] = str(e)
                return results
            # Meta rejects some images; the text still deserves to go out.
            log.warning(f"[Threads] Root with image failed ({e}). Retrying without image...")
            try:
                new_id = _publish_text(text, None, None)
            except Exception as e2:
                log.error(f"[Threads] Chain aborted at root: {e2}")
                results["error"] = str(e2)
                return results

        results["part_ids"].append(new_id)
        if is_root:
            root_id = new_id
        parent_id = new_id
        if idx < len(clean_parts) - 1:
            time.sleep(part_delay)

    results["post_id"] = root_id

    if bonus_reply and bonus_reply.strip():
        log.info("[Threads] Publishing bonus reply under root...")
        try:
            results["bonus_reply_id"] = _publish_text(bonus_reply.strip(), None, root_id)
        except Exception as e:
            # The thread is already live and readable — a failed bonus is a lost
            # nudge, not a broken post.
            log.warning(f"[Threads] Bonus reply failed: {e}")
            results["bonus_reply_error"] = str(e)

    log.info(f"[Threads] Chain published: post={root_id}, parts={results['part_ids']}")
    return results


if __name__ == '__main__':
    # Quick test
    test_result = post_threads(
        hook_text='pov: lu lagi di fase cape bgt tapi harus tetep produktif 💀\nistg setiap pagi di krl itu ujian mental sih wkwk',
        reply_text='mumpung rame sender numpang lapak ya gais 🙏\nada yang lagi nyari kipas mini portable? ini literally penyelamat hidup gue di krl\nharga terjangkau banget worth it parah, link di bio!',
        vibe_image_url='https://i.pinimg.com/736x/3f/01/5b/3f015bd219e4f2d4b5f96e7f474a8add.jpg',
    )
    print('[Test Result]', test_result)
