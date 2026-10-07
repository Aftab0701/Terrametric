import io
import zipfile
from pathlib import Path

import pytest

from app.config import Settings
from app.errors import FileTooLarge, InvalidArchive, UnsupportedFileType
from app.services.ingest import save_capped, stage_dataset


def make_zip(path: Path, members: dict[str, bytes]) -> Path:
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in members.items():
            zf.writestr(name, data)
    return path


def stage(tmp_path, name, settings=None):
    return stage_dataset(tmp_path / name, name, tmp_path / "work", settings or Settings())


@pytest.fixture(autouse=True)
def _workdir(tmp_path):
    (tmp_path / "work").mkdir()


def test_unsupported_extension(tmp_path):
    (tmp_path / "a.geojson").write_text("{}")
    with pytest.raises(UnsupportedFileType):
        stage(tmp_path, "a.geojson")


def test_zip_extension_but_not_a_zip(tmp_path):
    (tmp_path / "a.zip").write_text("definitely not a zip")
    with pytest.raises(InvalidArchive, match="not a valid zip"):
        stage(tmp_path, "a.zip")


def test_kml_extension_but_actually_zip(tmp_path):
    make_zip(tmp_path / "a.kml", {"x.txt": b"hi"})
    with pytest.raises(InvalidArchive, match="kmz"):
        stage(tmp_path, "a.kml")


def test_kml_without_kml_header(tmp_path):
    (tmp_path / "a.kml").write_text("just some text")
    with pytest.raises(InvalidArchive, match="does not look like KML"):
        stage(tmp_path, "a.kml")


def test_zip_with_nothing_useful(tmp_path):
    make_zip(tmp_path / "a.zip", {"readme.txt": b"hi"})
    with pytest.raises(InvalidArchive, match="neither"):
        stage(tmp_path, "a.zip")


def test_incomplete_shapefile_names_the_missing_parts(tmp_path):
    make_zip(tmp_path / "a.zip", {"roads.shp": b"x", "roads.dbf": b"x"})
    with pytest.raises(InvalidArchive, match=r"missing \.shx"):
        stage(tmp_path, "a.zip")


def test_two_shapefiles_are_rejected_as_ambiguous(tmp_path):
    make_zip(tmp_path / "a.zip", {f"{n}.{e}": b"x" for n in ("a", "b") for e in ("shp", "shx", "dbf")})
    with pytest.raises(InvalidArchive, match="2 shapefiles"):
        stage(tmp_path, "a.zip")


def test_zip_slip_member_names_never_reach_the_filesystem(tmp_path):
    make_zip(
        tmp_path / "evil.zip",
        {"../../evil.shp": b"A", "../../evil.shx": b"B", "../../evil.dbf": b"C", "..\\..\\evil.prj": b"D"},
    )
    staged = stage(tmp_path, "evil.zip")
    work = tmp_path / "work"
    assert staged.path.parent == work and staged.path.name == "dataset.shp"
    assert not (tmp_path.parent / "evil.shp").exists()
    assert not (tmp_path / "evil.shp").exists()
    work_files = sorted(p.name for p in work.iterdir() if p.name != "evil.zip")
    assert "dataset.shp" in work_files
    assert "dataset.dbf" in work_files
    assert "dataset.shx" in work_files
    assert not (tmp_path.parent / "evil.prj").exists()


def test_sidecars_from_other_folders_or_names_are_ignored(tmp_path):
    make_zip(
        tmp_path / "a.zip",
        {"good/roads.shp": b"1", "good/roads.shx": b"2", "good/roads.dbf": b"3", "other/roads.prj": b"WRONG"},
    )
    stage(tmp_path, "a.zip")
    assert not (tmp_path / "work" / "dataset.prj").exists()


def test_zip_bomb_is_caught_before_extraction(tmp_path):
    make_zip(tmp_path / "bomb.zip", {"a.shp": b"\0" * 5_000_000, "a.shx": b"x", "a.dbf": b"x"})
    with pytest.raises(FileTooLarge, match="expand"):
        stage(tmp_path, "bomb.zip", Settings(max_unzipped_bytes=1_000_000))
    assert list((tmp_path / "work").iterdir()) == []


def test_too_many_members(tmp_path):
    make_zip(tmp_path / "many.zip", {f"f{i}.txt": b"x" for i in range(30)})
    with pytest.raises(InvalidArchive, match="limit"):
        stage(tmp_path, "many.zip", Settings(max_zip_members=10))


def test_kmz_uses_doc_kml_as_entry_point(tmp_path):
    kml = b'<?xml version="1.0"?><kml xmlns="http://www.opengis.net/kml/2.2"/>'
    make_zip(tmp_path / "a.kmz", {"other.kml": b"nope", "doc.kml": kml, "icon.png": b"\x89PNG"})
    staged = stage(tmp_path, "a.kmz")
    assert staged.fmt == "kmz" and staged.path.read_bytes() == kml


def test_upload_cap_stops_streaming_early():
    class Endless(io.RawIOBase):
        reads = 0

        def read(self, n=-1):
            Endless.reads += 1
            return b"x" * n

    with pytest.raises(FileTooLarge):
        save_capped(Endless(), Path("/tmp/_cap_test"), limit=3 * 1024 * 1024)
    assert Endless.reads <= 5
