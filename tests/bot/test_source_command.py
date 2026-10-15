"""tests/bot/test_source_command.py"""

from unittest.mock import MagicMock, patch

import src.bot.tiktok_agent as agent


def test_source_command_dispatches_url_and_count():
    captured = {}

    with patch.object(agent, "_trigger_source_pipeline_async",
                      side_effect=lambda args, mid, tid: captured.update(args=args, mid=mid, tid=tid)):
        agent.handle_tiktok_message({"message_id": 1}, "/source https://youtu.be/ZRIBNLUXbZo 4", 1, 83)

    assert captured["args"] == "https://youtu.be/ZRIBNLUXbZo 4"
    assert captured["tid"] == 83


def test_source_command_without_url_prompts_format():
    sent = []
    with patch.object(agent, "_send_reply", side_effect=lambda *a, **k: sent.append(a[0])):
        agent.handle_tiktok_message({"message_id": 1}, "/source", 1, 83)
    assert sent and "/source" in sent[0]


def test_trigger_rejects_non_url():
    sent = []
    with patch.object(agent, "_send_reply", side_effect=lambda *a, **k: sent.append(a[0])):
        agent._trigger_source_pipeline_async("bukan url", 1, 83)
    assert sent and "/source" in sent[0]


def test_trigger_clamps_count_to_8():
    captured = {}

    def fake_thread(target=None, daemon=None, name=None, **kw):
        captured["started"] = True

        class T:
            def start(self):
                pass

        return T()

    sent = []
    # ADJUSTED: `_thread.lock.locked` is read-only, so it cannot be patched in
    # place. The module-level lock name is swapped for a free (unlocked) mock.
    free_lock = MagicMock()
    free_lock.locked.return_value = False
    with patch.object(agent, "_send_reply", side_effect=lambda *a, **k: sent.append(a[0])), \
         patch.object(agent.threading, "Thread", side_effect=fake_thread), \
         patch.object(agent, "_PIPELINE_LOCK", free_lock):
        agent._trigger_source_pipeline_async("https://youtu.be/x 99", 1, 83)

    assert captured.get("started"), "worker thread should have been created"
    assert any("8" in m for m in sent), f"count should be clamped to 8, got {sent}"
