from src.clipper.framing import determine_framing_mode


def test_framing_detects_head_near_top_and_falls_back():
    # Face top at y=40 on 1080p (less than 8% bound = 86px) -> danger of head cut
    detections = [{"x": 900, "y": 40, "w": 180, "h": 220}]
    cfg = determine_framing_mode(1920, 1080, face_boxes=detections)
    assert cfg.mode == "ambient_16_9"
    assert "head crop" in cfg.reason


def test_framing_detects_normal_centered_face():
    # Face placed comfortably inside safe margins
    detections = [{"x": 900, "y": 200, "w": 180, "h": 220}]
    cfg = determine_framing_mode(1920, 1080, face_boxes=detections)
    assert cfg.mode == "crop_4_3"
