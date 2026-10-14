"""Unit tests for subtitle_detector.py."""

import os
import tempfile
import numpy as np
import cv2
import pytest

from src.tiktok.subtitle_detector import (
    _compute_edge_density,
    _check_text_contrast,
    has_hardcoded_subtitles,
    has_soft_subtitles,
    has_existing_subtitles,
)


def _make_dummy_frame(has_text: bool, width: int = 720, height: int = 1280) -> str:
    """Create a temporary PNG image with or without text in the subtitle zone."""
    img = np.zeros((height, width, 3), dtype=np.uint8)
    # Add some natural background texture
    img[:, :] = [40, 40, 40]

    if has_text:
        # Add high-contrast white text in bottom 25% (subtitle zone ~ y=832 to 1177)
        y_pos = int(height * 0.78)
        cv2.putText(
            img,
            "INI ADALAH SUBTITLE BAWAAN VIDEO",
            (50, y_pos),
            cv2.FONT_HERSHEY_SIMPLEX,
            1.1,
            (255, 255, 255),
            3,
            cv2.LINE_AA,
        )
        cv2.putText(
            img,
            "BARIS KEDUA SUBTITLE TEKS JELAS",
            (50, y_pos + 50),
            cv2.FONT_HERSHEY_SIMPLEX,
            1.0,
            (255, 255, 255),
            3,
            cv2.LINE_AA,
        )

    fd, path = tempfile.mkstemp(suffix=".png")
    os.close(fd)
    cv2.imwrite(path, img)
    return path


def test_edge_density_distinguishes_subtitles():
    frame_with_text = _make_dummy_frame(has_text=True)
    frame_plain = _make_dummy_frame(has_text=False)

    try:
        density_text = _compute_edge_density(frame_with_text)
        density_plain = _compute_edge_density(frame_plain)

        assert density_text > density_plain
        assert density_text > 0.01  # Has measurable edges
        assert density_plain < 0.005  # Near zero edges
    finally:
        os.remove(frame_with_text)
        os.remove(frame_plain)


def test_contrast_check_distinguishes_subtitles():
    frame_with_text = _make_dummy_frame(has_text=True)
    frame_plain = _make_dummy_frame(has_text=False)

    try:
        contrast_text = _check_text_contrast(frame_with_text)
        contrast_plain = _check_text_contrast(frame_plain)

        assert bool(contrast_text) is True
        assert bool(contrast_plain) is False
    finally:
        os.remove(frame_with_text)
        os.remove(frame_plain)


def test_has_hardcoded_subtitles_nonexistent_file():
    assert has_hardcoded_subtitles("non_existent_path.mp4") is False
    assert has_soft_subtitles("non_existent_path.mp4") is False
    assert has_existing_subtitles("non_existent_path.mp4") is False
