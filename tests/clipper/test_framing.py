import pytest
from src.clipper.framing import determine_framing_mode, CropConfig

def test_framing_fallback_when_subjects_at_edges():
    # Two faces detected at x=200 and x=1700 on 1920 width
    detections = [{"x": 200, "w": 100}, {"x": 1700, "w": 100}]
    cfg = determine_framing_mode(1920, 1080, face_boxes=detections)
    assert cfg.mode == "ambient_16_9"
    assert cfg.crop_filter is None

def test_framing_4_3_when_single_face_centered():
    detections = [{"x": 860, "w": 200}]  # centered face
    cfg = determine_framing_mode(1920, 1080, face_boxes=detections)
    assert cfg.mode == "crop_4_3"
    assert cfg.crop_filter is not None
    assert "crop=1440:1080" in cfg.crop_filter

def test_framing_4_3_pans_towards_angled_face():
    detections = [{"x": 1200, "w": 200}]  # face on the right (center ~1300)
    cfg = determine_framing_mode(1920, 1080, face_boxes=detections)
    assert cfg.mode == "crop_4_3"
    assert "crop=1440:1080" in cfg.crop_filter
    # Default center offset for 1440 crop is (1920-1440)/2 = 240
    # Since person is at x=1200..1400, x_offset should shift right (> 240)
    assert cfg.x_offset > 240
