"""
Threads Publisher — posts text + optional image to Threads via API.
Strategy: post utama (vibe hook, no link) → reply (produk + link afiliasi)
"""

import os
import time
import requests
from dotenv import load_dotenv
from pathlib import Path

load_dotenv(Path(__file__).parent.parent.parent / '.env')

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

    r = requests.post(f'{BASE_URL}/{USER_ID}/threads', params=params)
    r.raise_for_status()
    return r.json()['id']


def _publish_container(container_id: str) -> str:
    """Publish a container, return published post ID."""
    r = requests.post(
        f'{BASE_URL}/{USER_ID}/threads_publish',
        params={
            'creation_id': container_id,
            'access_token': ACCESS_TOKEN,
        }
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
        print('[Threads] Creating main post container...')
        main_container = _create_container(hook_text, image_url=vibe_image_url)
        time.sleep(2)  # Threads recommends waiting before publish

        print('[Threads] Publishing main post...')
        post_id = _publish_container(main_container)
        results['post_id'] = post_id
        print(f'[Threads] Main post published: {post_id}')

        time.sleep(3)  # Wait before reply

        # Reply — optional (skip if reply_text is None/empty)
        if reply_text:
            print('[Threads] Creating reply container...')
            reply_container = _create_container(
                reply_text,
                image_url=product_image_url,
                reply_to_id=post_id
            )
            time.sleep(2)

            print('[Threads] Publishing reply...')
            reply_id = _publish_container(reply_container)
            results['reply_id'] = reply_id
            print(f'[Threads] Reply published: {reply_id}')

    except requests.HTTPError as e:
        print(f'[Threads] HTTP Error: {e.response.status_code} {e.response.text}')
        results['error'] = e.response.text
    except Exception as e:
        print(f'[Threads] Error: {e}')
        results['error'] = str(e)

    return results


if __name__ == '__main__':
    # Quick test
    test_result = post_threads(
        hook_text='pov: lu lagi di fase cape bgt tapi harus tetep produktif 💀\nistg setiap pagi di krl itu ujian mental sih wkwk',
        reply_text='mumpung rame sender numpang lapak ya gais 🙏\nada yang lagi nyari kipas mini portable? ini literally penyelamat hidup gue di krl\nharga terjangkau banget worth it parah, link di bio!',
        vibe_image_url='https://i.pinimg.com/736x/3f/01/5b/3f015bd219e4f2d4b5f96e7f474a8add.jpg',
    )
    print('[Test Result]', test_result)
