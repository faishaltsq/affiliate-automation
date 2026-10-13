import pytest
from src.clipper.story_analyzer import build_multi_story_packages


def test_build_multi_story_packages(tmp_path):
    f1 = tmp_path / "jual_1.mp4"
    f2 = tmp_path / "jual_2.mp4"
    outro = tmp_path / "outro.mp4"
    f1.write_text("data")
    f2.write_text("data")
    outro.write_text("data")

    punchlines = [
        {"target_file": str(f1), "punchline_hook": "HOOK 1", "climax_start": 5.0, "climax_duration": 3.5},
        {"target_file": str(f2), "punchline_hook": "HOOK 2", "climax_start": 8.0, "climax_duration": 3.5},
    ]

    packages = build_multi_story_packages(
        footages=[str(f1), str(f2), str(outro)],
        punchlines=punchlines,
        outro_file=str(outro),
        target_duration=60.0
    )

    assert len(packages) == 2
    assert packages[0]["hero_file"] == str(f1)
    assert packages[0]["punchline_hook"] == "HOOK 1"
    assert packages[1]["hero_file"] == str(f2)
    assert packages[1]["punchline_hook"] == "HOOK 2"
    assert "story_beats" in packages[0]
    assert "story_beats" in packages[1]
