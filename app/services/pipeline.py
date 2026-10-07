from __future__ import annotations

import logging
from collections import Counter
from datetime import datetime, timezone
from typing import Any

from shapely.geometry import mapping
from sqlalchemy import insert
from sqlalchemy.orm import Session

from ..config import Settings
from ..errors import InternalProcessingError, ProcessingError
from ..models import FeatureRecord, FileRecord, FileStatus, MeasurementStatus
from .crs import describe_crs
from .ingest import StagedDataset
from .measure import Measurement, MeasurementError, Measurer
from .readers import RawFeature, read_dataset

log = logging.getLogger(__name__)
BATCH_SIZE = 1000


def process_dataset(session: Session, record: FileRecord, staged: StagedDataset, settings: Settings) -> None:
    try:
        dataset = read_dataset(staged.path, staged.fmt, settings.max_features)
        crs_label = describe_crs(dataset.crs)
        measurer = Measurer(dataset.crs)

        geom_types: Counter[str] = Counter()
        statuses: Counter[str] = Counter()
        totals = {"area_m2": 0.0, "perimeter_m": 0.0, "length_m": 0.0}
        worst_deviation = 0.0
        batch: list[dict[str, Any]] = []

        for feat in dataset.features:
            measurement = _measure_feature(measurer, feat)
            gtype = _geometry_type(feat)
            geom_types[gtype] += 1
            statuses[measurement.status.value] += 1
            for key in totals:
                totals[key] += getattr(measurement, key) or 0.0
            worst_deviation = max(worst_deviation, measurement.max_deviation_pct or 0.0)

            batch.append(
                {
                    "file_id": record.id,
                    "feature_index": feat.index,
                    "layer": feat.layer,
                    "geometry_type": gtype,
                    "geometry": _geometry_json(feat),
                    "crs": crs_label,
                    "properties": feat.properties,
                    "measurement_status": measurement.status.value,
                    "measurement": measurement.to_dict(),
                }
            )
            if len(batch) >= BATCH_SIZE:
                session.execute(insert(FeatureRecord), batch)
                batch.clear()
        if batch:
            session.execute(insert(FeatureRecord), batch)

        record.crs = crs_label
        record.feature_count = len(dataset.features)
        record.warnings = dataset.warnings
        record.summary = {
            "geometry_types": dict(geom_types),
            "measurement_status": dict(statuses),
            "total_area_m2": round(totals["area_m2"], 4),
            "total_perimeter_m": round(totals["perimeter_m"], 4),
            "total_length_m": round(totals["length_m"], 4),
            "max_deviation_pct": round(worst_deviation, 6),
        }
        record.status = FileStatus.COMPLETED.value
        record.completed_at = datetime.now(timezone.utc)
        session.commit()

    except ProcessingError as exc:
        session.rollback()
        _mark_failed(session, record, exc.message)
        exc.extra["file_id"] = record.id
        raise
    except Exception as exc:
        session.rollback()
        log.exception("Unexpected failure processing file %s", record.id)
        _mark_failed(session, record, "Unexpected internal error while processing the file.")
        raise InternalProcessingError(
            "Unexpected internal error while processing the file.", file_id=record.id
        ) from exc


def _measure_feature(measurer: Measurer, feat: RawFeature) -> Measurement:
    if feat.read_error:
        return Measurement(MeasurementStatus.UNSUPPORTED, notes=[feat.read_error])
    try:
        return measurer.measure(feat.geometry)
    except MeasurementError as exc:
        return Measurement(MeasurementStatus.FAILED, notes=[str(exc)])
    except Exception as exc:
        log.warning("Measuring feature %s failed: %s", feat.index, exc, exc_info=True)
        return Measurement(MeasurementStatus.FAILED, notes=[f"Unexpected error: {exc}"])


def _geometry_type(feat: RawFeature) -> str:
    return feat.geometry.geom_type if feat.geometry is not None else "None"


def _geometry_json(feat: RawFeature) -> dict[str, Any] | None:
    g = feat.geometry
    return None if g is None or g.is_empty else mapping(g)


def _mark_failed(session: Session, record: FileRecord, message: str) -> None:
    record.status = FileStatus.FAILED.value
    record.error = message
    record.completed_at = datetime.now(timezone.utc)
    session.commit()
