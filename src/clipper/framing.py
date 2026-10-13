import os
import subprocess
import tempfile
from dataclasses import dataclass
from typing import Optional, List, Dict

try:
    import cv2
except ImportError:
    cv2 = None


@dataclass
class CropConfig:
    mode: str  # "crop_4_3", "crop_9_16", or "ambient_16_9"
    crop_filter: Optional[str] = None
    x_offset: int = 0
    y_offset: int = 0
    target_w: int = 1080
    target_h: int = 810
    margin_v: int = 580
    reason: str = ""


def detect_faces_in_image(image_bgr) -> List[Dict[str, int]]:
    """Detect faces in a BGR image using OpenCV Haar cascade."""
    if cv2 is None or image_bgr is None:
        return []

    xml_path = os.path.join(cv2.data.haarcascades, "haarcascade_frontalface_default.xml")
    if not os.path.exists(xml_path):
        return []

    cascade = cv2.CascadeClassifier(xml_path)
    if cascade.empty():
        return []

    gray = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2GRAY)
    # Downscale for fast detection if high res
    h, w = gray.shape[:2]
    scale = 1.0
    if w > 1280:
        scale = 1280.0 / w
        gray = cv2.resize(gray, (int(w * scale), int(h * scale)))

    faces = cascade.detectMultiScale(
        gray,
        scaleFactor=1.1,
        minNeighbors=4,
        minSize=(int(30 * scale), int(30 * scale)),
    )

    results = []
    for (x, y, fw, fh) in faces:
        results.append({
            "x": int(x / scale),
            "y": int(y / scale),
            "w": int(fw / scale),
            "h": int(fh / scale),
        })
    return results


def detect_faces_in_video(video_path: str, num_samples: int = 3) -> List[Dict[str, int]]:
    """Sample keyframes from video and detect faces."""
    if cv2 is None or not os.path.exists(video_path):
        return []

    from src.clipper.editor import get_video_duration
    dur = get_video_duration(video_path)
    if dur <= 0:
        return []

    sample_times = [dur * (i + 1) / (num_samples + 1) for i in range(num_samples)]
    detected_boxes = []

    with tempfile.TemporaryDirectory() as tmp_dir:
        for i, t in enumerate(sample_times):
            frame_path = os.path.join(tmp_dir, f"frame_{i}.jpg")
            cmd = [
                "ffmpeg", "-y", "-ss", f"{t:.2f}",
                "-i", video_path,
                "-vframes", "1",
                "-q:v", "3",
                frame_path
            ]
            try:
                subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
                if os.path.exists(frame_path):
                    img = cv2.imread(frame_path)
                    if img is not None:
                        boxes = detect_faces_in_image(img)
                        detected_boxes.extend(boxes)
            except Exception:
                continue

    return detected_boxes


def determine_framing_mode(
    in_w: int,
    in_h: int,
    face_boxes: Optional[List[Dict[str, int]]] = None,
    preferred_mode: str = "auto",
) -> CropConfig:
    """
    Decide whether to crop to 4:3, 9:16, or fall back to standard 16:9 ambient pillarbox.
    Rule:
    - If cropping would cut off subjects on the edges, fall back to ambient_16_9 ("seperti biasanya saja").
    - If subject(s) fit safely, crop to 4:3 (or 9:16) and pan towards the subject's center angle.
    """
    if face_boxes is None:
        face_boxes = []

    # If already vertical (9:16), keep as is
    if in_h > in_w:
        return CropConfig(
            mode="fullscreen_9_16",
            crop_filter=None,
            target_w=1080,
            target_h=1920,
            margin_v=380,
            reason="Input video is already vertical (9:16).",
        )

    # 4:3 Crop Geometry on 16:9 source (1920x1080 -> 1440x1080)
    crop_w_4_3 = int(in_h * 4.0 / 3.0)  # e.g., 1080 * 4/3 = 1440
    if crop_w_4_3 > in_w:
        crop_w_4_3 = in_w

    default_x_offset_4_3 = max(0, (in_w - crop_w_4_3) // 2)

    # If no faces detected, default to 4:3 center crop
    if not face_boxes:
        return CropConfig(
            mode="crop_4_3",
            crop_filter=f"crop={crop_w_4_3}:{in_h}:{default_x_offset_4_3}:0",
            x_offset=default_x_offset_4_3,
            y_offset=0,
            target_w=1080,
            target_h=int(1080 * 3.0 / 4.0),  # 810
            margin_v=480,
            reason="No faces detected; applied 4:3 center crop.",
        )

    # Compute bounding span of all detected faces
    min_x = min(b["x"] for b in face_boxes)
    max_x = max(b["x"] + b["w"] for b in face_boxes)
    span = max_x - min_x

    # Check edge cut-off risk
    # If any face is located at the far margins (e.g. < 12% or > 88% of width)
    # or span of multiple faces exceeds 4:3 crop width with safe padding
    safe_left_bound = int(in_w * 0.12)
    safe_right_bound = int(in_w * 0.88)
    at_extreme_left = any(b["x"] < safe_left_bound for b in face_boxes)
    at_extreme_right = any((b["x"] + b["w"]) > safe_right_bound for b in face_boxes)

    # If subjects are spread across both edges, cropping will cut someone off!
    if (at_extreme_left and at_extreme_right) or (span > (crop_w_4_3 - 100)):
        return CropConfig(
            mode="ambient_16_9",
            crop_filter=None,
            x_offset=0,
            y_offset=0,
            target_w=1080,
            target_h=int(in_h * 1080.0 / in_w),  # 608 for 16:9
            margin_v=580,
            reason="Multiple subjects at frame edges; fallback to ambient 16:9 to avoid cutoff.",
        )

    # Subject(s) can fit inside 4:3 crop! Calculate optimal pan offset
    face_centers = [b["x"] + b["w"] / 2.0 for b in face_boxes]
    avg_center_x = sum(face_centers) / len(face_centers)

    # Calculate x_offset centered on avg_center_x
    desired_x_off = int(avg_center_x - crop_w_4_3 / 2.0)
    # Clamp within [0, in_w - crop_w_4_3]
    clamped_x_off = max(0, min(in_w - crop_w_4_3, desired_x_off))

    return CropConfig(
        mode="crop_4_3",
        crop_filter=f"crop={crop_w_4_3}:{in_h}:{clamped_x_off}:0",
        x_offset=clamped_x_off,
        y_offset=0,
        target_w=1080,
        target_h=int(1080 * 3.0 / 4.0),  # 810
        margin_v=480,
        reason=f"Cropped to 4:3 panned towards subject (center={int(avg_center_x)}px).",
    )
