from __future__ import annotations

import shutil
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import BinaryIO

from ..config import Settings
from ..errors import FileTooLarge, InvalidArchive, UnsupportedFileType

ALLOWED_EXTENSIONS = {".zip", ".kml", ".kmz"}
SHAPEFILE_REQUIRED = (".shp", ".shx", ".dbf")
SHAPEFILE_OPTIONAL = (".prj", ".cpg")
CHUNK_SIZE = 1024 * 1024


@dataclass(frozen=True)
class StagedDataset:
    path: Path
    fmt: str


def save_capped(src: BinaryIO, dest: Path, limit: int) -> int:
    written = 0
    with open(dest, "wb") as out:
        while chunk := src.read(CHUNK_SIZE):
            written += len(chunk)
            if written > limit:
                raise FileTooLarge(f"Upload exceeds the {limit // (1024 * 1024)} MB limit.")
            out.write(chunk)
    return written


def stage_dataset(upload: Path, original_name: str, workdir: Path, settings: Settings) -> StagedDataset:
    ext = Path(original_name).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise UnsupportedFileType(
            f"Unsupported file type '{ext or original_name}'. Upload a .zip containing a "
            "Shapefile, or a .kml / .kmz file."
        )

    with open(upload, "rb") as fh:
        head = fh.read(4096)
    is_zip = head.startswith(b"PK\x03\x04")

    if ext == ".kml":
        if is_zip:
            raise InvalidArchive("This .kml file is actually a zip archive. Rename it to .kmz.")
        if b"<kml" not in head.lower() and b"<?xml" not in head.lower():
            raise InvalidArchive("File does not look like KML (no XML/<kml> header found).")
        dest = workdir / "dataset.kml"
        shutil.move(upload, dest)
        return StagedDataset(dest, "kml")

    if not is_zip:
        raise InvalidArchive(f"File has a {ext} extension but is not a valid zip archive.")
    try:
        with zipfile.ZipFile(upload) as zf:
            return _unpack(zf, workdir, settings, expect_kmz=(ext == ".kmz"))
    except zipfile.BadZipFile as exc:
        raise InvalidArchive(f"Corrupt zip archive: {exc}") from exc


def _unpack(zf: zipfile.ZipFile, workdir: Path, settings: Settings, expect_kmz: bool) -> StagedDataset:
    members = [
        i
        for i in zf.infolist()
        if not i.is_dir() and "__MACOSX" not in i.filename and not Path(i.filename).name.startswith(".")
    ]
    if len(members) > settings.max_zip_members:
        raise InvalidArchive(f"Archive has {len(members)} files; the limit is {settings.max_zip_members}.")
    if sum(i.file_size for i in members) > settings.max_unzipped_bytes:
        raise FileTooLarge(
            f"Archive would expand beyond {settings.max_unzipped_bytes // (1024 * 1024)} MB."
        )

    def posix(info: zipfile.ZipInfo) -> PurePosixPath:
        return PurePosixPath(info.filename)

    shp = [i for i in members if posix(i).suffix.lower() == ".shp"]
    kml = [i for i in members if posix(i).suffix.lower() == ".kml"]

    if shp:
        if len(shp) > 1:
            raise InvalidArchive(
                f"Archive contains {len(shp)} shapefiles; upload one shapefile per request."
            )
        return _extract_shapefile(zf, shp[0], members, workdir, settings, posix)
    if kml:
        kml.sort(key=lambda i: (posix(i).name.lower() != "doc.kml", posix(i).as_posix()))
        dest = workdir / "dataset.kml"
        _extract_member(zf, kml[0], dest, settings.max_unzipped_bytes)
        return StagedDataset(dest, "kmz")

    raise InvalidArchive("Archive contains neither a .shp shapefile nor a .kml file.")


def _extract_shapefile(zf, shp_info, members, workdir, settings, posix) -> StagedDataset:
    stem = posix(shp_info).stem.lower()
    folder = posix(shp_info).parent
    siblings = {
        posix(i).suffix.lower(): i
        for i in members
        if posix(i).parent == folder and posix(i).stem.lower() == stem
    }
    missing = [e for e in SHAPEFILE_REQUIRED if e not in siblings]
    if missing:
        raise InvalidArchive(
            f"Shapefile is incomplete: missing {', '.join(missing)}. A shapefile needs "
            ".shp, .shx and .dbf (and should include .prj for its CRS)."
        )
    budget = settings.max_unzipped_bytes
    for ext in SHAPEFILE_REQUIRED + SHAPEFILE_OPTIONAL:
        if ext in siblings:
            budget -= _extract_member(zf, siblings[ext], workdir / f"dataset{ext}", budget)
    return StagedDataset(workdir / "dataset.shp", "shapefile")


def _extract_member(zf: zipfile.ZipFile, info: zipfile.ZipInfo, dest: Path, budget: int) -> int:
    written = 0
    with zf.open(info) as src, open(dest, "wb") as out:
        while chunk := src.read(CHUNK_SIZE):
            written += len(chunk)
            if written > budget:
                raise FileTooLarge("Archive expands beyond the configured limit (possible zip bomb).")
            out.write(chunk)
    return written
