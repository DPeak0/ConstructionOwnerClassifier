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
    build_venv = tmp_path / ".build-venv"
    build_venv.mkdir()
    (build_venv / "dependency.jpg").write_bytes(b"jpg")

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


def test_scan_mixed_files_and_directories_deduplicates_and_supports_jfif(tmp_path: Path):
    folder = tmp_path / "photos"
    folder.mkdir()
    first = folder / "first.jpg"
    second = folder / "second.JFIF"
    first.write_bytes(b"jpg")
    second.write_bytes(b"jfif")
    unsupported = tmp_path / "notes.txt"
    unsupported.write_text("ignore", encoding="utf-8")

    found = scan_images([folder, first, unsupported])

    assert found == [first.resolve(), second.resolve()]


def test_output_parent_does_not_hide_selected_input_directory(tmp_path: Path):
    folder = tmp_path / "photos"
    folder.mkdir()
    image = folder / "visible.jpg"
    image.write_bytes(b"jpg")

    assert scan_images(folder, tmp_path) == [image.resolve()]


def test_output_directory_inside_input_is_excluded(tmp_path: Path):
    source = tmp_path / "photos"
    output = source / "results"
    output.mkdir(parents=True)
    wanted = source / "wanted.jpg"
    generated = output / "generated.jpg"
    wanted.write_bytes(b"jpg")
    generated.write_bytes(b"jpg")

    assert scan_images(source, output) == [wanted.resolve()]


def test_empty_source_list_is_rejected():
    try:
        scan_images([])
    except ValueError as exc:
        assert "尚未选择" in str(exc)
    else:
        raise AssertionError("empty source list should fail")
