import pytest

from vision.ui_detector import UIDetectorError, parse_vision_response


def test_parse_vision_response_single_object() -> None:
    raw = '{"element":"login_button","x":540,"y":320,"confidence":0.92}'

    detections = parse_vision_response(raw, image_width=1920, image_height=1080)

    assert len(detections) == 1
    assert detections[0].element == "login_button"
    assert detections[0].x == 540
    assert detections[0].y == 320
    assert detections[0].confidence == pytest.approx(0.92)


def test_parse_vision_response_multiple_sorted_by_confidence() -> None:
    raw = """
    [
      {"label":"email_field","x":420,"y":260,"confidence":0.81},
      {"label":"login_button","x":540,"y":320,"confidence":0.93}
    ]
    """

    detections = parse_vision_response(raw, image_width=1920, image_height=1080)

    assert len(detections) == 2
    assert detections[0].element == "login_button"
    assert detections[1].element == "email_field"


def test_parse_vision_response_out_of_bounds_raises() -> None:
    raw = '{"element":"login_button","x":4000,"y":320,"confidence":0.92}'

    with pytest.raises(UIDetectorError):
        parse_vision_response(raw, image_width=1920, image_height=1080)
