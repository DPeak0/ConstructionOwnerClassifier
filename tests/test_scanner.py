from pathlib import Path

from owner_classifier.scanner import scan_images


def test_scan_supported_images_and_exclude_results(tmp_path: Path):
    (tmp_path / "a.jpg").write_bytes(b"jpg")
    (tmp_path / "b.PNG").write_bytes(b"png")
    (tmp_path / "notes.txt").write_text("ignore", encoding="utf-8")
    results = tmp_path / "分类结果_20260808_120000"
    results.mkdir()
    (results / "copied.jpg").write_bytes(b"jpg")
    build = tmp_path / "build"
    build.mkdir()
    (build / "artifact.jpg").write_bytes(b"jpg")

    found = scan_images(tmp_path)
    assert [path.name for path in found] == ["a.jpg", "b.PNG"]


def test_missing_input_is_rejected(tmp_path: Path):
    missing = tmp_path / "missing"
    try:
        scan_images(missing)
    except ValueError as exc:
        assert "输入目录不存在" in str(exc)
    else:
        raise AssertionError("missing directory should fail")
