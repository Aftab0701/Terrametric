from __future__ import annotations

import secrets
import tempfile
from pathlib import Path, PurePosixPath
from typing import Iterator

from fastapi import APIRouter, Depends, File, Query, Request, Response, UploadFile
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import schemas
from ..config import Settings
from ..errors import NotFound, NotReady
from ..models import FeatureRecord, FileRecord, FileStatus, MeasurementStatus
from ..services import units as U
from ..services.ingest import save_capped, stage_dataset
from ..services.pipeline import process_dataset

router = APIRouter(prefix="/api/files", tags=["files"])


def get_session(request: Request) -> Iterator[Session]:
    with request.app.state.session_factory() as session:
        yield session


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def _get_file(session: Session, file_id: str) -> FileRecord:
    record = session.get(FileRecord, file_id)
    if record is None:
        raise NotFound(f"No file with id '{file_id}'.")
    return record


def _get_ready_file(session: Session, file_id: str) -> FileRecord:
    record = _get_file(session, file_id)
    if record.status != FileStatus.COMPLETED.value:
        raise NotReady(
            f"File '{file_id}' is {record.status}; results are only available once it is COMPLETED.",
            status=record.status,
        )
    return record


@router.post("/", response_model=schemas.FileOut, status_code=201, summary="Upload and process a file")
def upload_file(
    file: UploadFile = File(..., description="A .zip containing a Shapefile, or a .kml / .kmz file."),
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
):
    filename = PurePosixPath((file.filename or "upload").replace("\\", "/")).name[:255] or "upload"

    with tempfile.TemporaryDirectory(prefix="terrametric-", ignore_cleanup_errors=True) as tmp:
        workdir = Path(tmp)
        raw = workdir / "upload.bin"
        size = save_capped(file.file, raw, settings.max_upload_bytes)
        staged = stage_dataset(raw, filename, workdir, settings)

        record = FileRecord(
            id=secrets.token_hex(6),
            filename=filename,
            source_format=staged.fmt,
            size_bytes=size,
            warnings=[],
        )
        session.add(record)
        session.commit()

        process_dataset(session, record, staged, settings)
    session.refresh(record)
    return record


@router.get("/", response_model=schemas.FileList, summary="List uploaded files")
def list_files(
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    session: Session = Depends(get_session),
):
    total = session.scalar(select(func.count()).select_from(FileRecord)) or 0
    rows = session.scalars(
        select(FileRecord).order_by(FileRecord.created_at.desc()).limit(limit).offset(offset)
    ).all()
    return {"total": total, "limit": limit, "offset": offset, "items": rows}


@router.get("/{file_id}/", response_model=schemas.FileOut, summary="File information")
def get_file(file_id: str, session: Session = Depends(get_session)):
    return _get_file(session, file_id)


@router.delete("/{file_id}/", status_code=204, summary="Delete a file and all its features")
def delete_file(file_id: str, session: Session = Depends(get_session)):
    session.delete(_get_file(session, file_id))
    session.commit()
    return Response(status_code=204)


@router.get("/{file_id}/features/", response_model=schemas.FeaturePage, summary="Extracted features")
def get_features(
    file_id: str,
    include_geometry: bool = Query(True, description="Set false for a lightweight listing."),
    geometry_type: str | None = Query(None, description="e.g. Polygon, MultiPolygon, LineString, Point"),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    session: Session = Depends(get_session),
):
    _get_ready_file(session, file_id)
    stmt = select(FeatureRecord).where(FeatureRecord.file_id == file_id)
    if geometry_type:
        stmt = stmt.where(FeatureRecord.geometry_type == geometry_type)
    total = session.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = session.scalars(stmt.order_by(FeatureRecord.feature_index).limit(limit).offset(offset)).all()
    items = [
        schemas.FeatureOut(
            index=r.feature_index,
            layer=r.layer,
            geometry_type=r.geometry_type,
            crs=r.crs,
            geometry=r.geometry if include_geometry else None,
            properties=r.properties,
        )
        for r in rows
    ]
    return schemas.FeaturePage(file_id=file_id, total=total, limit=limit, offset=offset, items=items)


@router.get(
    "/{file_id}/measurements/",
    response_model=schemas.MeasurementPage,
    summary="Per-feature measurements",
)
def get_measurements(
    file_id: str,
    area_unit: U.AreaUnit = Query("m2"),
    length_unit: U.LengthUnit = Query("m", description="Also used for polygon perimeters."),
    geometry_type: str | None = Query(None),
    status: MeasurementStatus | None = Query(None, description="Filter by measurement status."),
    include_properties: bool = Query(False),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    session: Session = Depends(get_session),
):
    record = _get_ready_file(session, file_id)
    stmt = select(FeatureRecord).where(FeatureRecord.file_id == file_id)
    if geometry_type:
        stmt = stmt.where(FeatureRecord.geometry_type == geometry_type)
    if status:
        stmt = stmt.where(FeatureRecord.measurement_status == status.value)
    total = session.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = session.scalars(stmt.order_by(FeatureRecord.feature_index).limit(limit).offset(offset)).all()

    s = record.summary or {}
    return schemas.MeasurementPage(
        file_id=file_id,
        units=schemas.Units(area=area_unit, length=length_unit),
        totals=schemas.Totals(
            area=U.area(s.get("total_area_m2", 0.0), area_unit) or 0.0,
            perimeter=U.length(s.get("total_perimeter_m", 0.0), length_unit) or 0.0,
            length=U.length(s.get("total_length_m", 0.0), length_unit) or 0.0,
        ),
        total=total,
        limit=limit,
        offset=offset,
        items=[
            schemas.MeasuredFeature(
                index=r.feature_index,
                layer=r.layer,
                geometry_type=r.geometry_type,
                crs=r.crs,
                properties=r.properties if include_properties else None,
                measurement=_convert(r.measurement, area_unit, length_unit),
            )
            for r in rows
        ],
    )


def _convert(m: dict, area_unit: U.AreaUnit, length_unit: U.LengthUnit) -> schemas.MeasurementOut:
    g = m.get("geodesic", {})
    return schemas.MeasurementOut(
        status=m["status"],
        area=U.area(m.get("area_m2"), area_unit),
        perimeter=U.length(m.get("perimeter_m"), length_unit),
        length=U.length(m.get("length_m"), length_unit),
        geometry_repaired=m.get("geometry_repaired", False),
        methods=m.get("methods", {}),
        geodesic_check=schemas.GeodesicCheck(
            area=U.area(g.get("area_m2"), area_unit),
            perimeter=U.length(g.get("perimeter_m"), length_unit),
            length=U.length(g.get("length_m"), length_unit),
        ),
        max_deviation_pct=m.get("max_deviation_pct"),
        notes=m.get("notes", []),
    )
