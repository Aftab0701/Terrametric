from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class FileSummary(BaseModel):
    geometry_types: dict[str, int]
    measurement_status: dict[str, int]
    total_area_m2: float
    total_perimeter_m: float
    total_length_m: float
    max_deviation_pct: float = Field(
        description="Worst disagreement between the projected result and the independent "
        "geodesic check across all features (percent)."
    )


class FileOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    filename: str
    source_format: str
    status: str
    crs: str | None
    feature_count: int
    warnings: list[str]
    summary: FileSummary | None = None
    error: str | None = None
    created_at: datetime
    completed_at: datetime | None = None


class FileList(BaseModel):
    total: int
    limit: int
    offset: int
    items: list[FileOut]


class FeatureOut(BaseModel):
    index: int
    layer: str | None
    geometry_type: str
    crs: str | None
    geometry: dict[str, Any] | None = Field(
        description="GeoJSON-shaped geometry with coordinates in the feature's *source* CRS."
    )
    properties: dict[str, Any]


class FeaturePage(BaseModel):
    file_id: str
    total: int
    limit: int
    offset: int
    items: list[FeatureOut]


class Units(BaseModel):
    area: str
    length: str


class GeodesicCheck(BaseModel):
    area: float | None = None
    perimeter: float | None = None
    length: float | None = None


class MeasurementOut(BaseModel):
    status: str
    area: float | None = None
    perimeter: float | None = None
    length: float | None = None
    geometry_repaired: bool = False
    methods: dict[str, str] = {}
    geodesic_check: GeodesicCheck = GeodesicCheck()
    max_deviation_pct: float | None = None
    notes: list[str] = []


class MeasuredFeature(BaseModel):
    index: int
    layer: str | None
    geometry_type: str
    crs: str | None
    properties: dict[str, Any] | None = None
    measurement: MeasurementOut


class Totals(BaseModel):
    area: float
    perimeter: float
    length: float


class MeasurementPage(BaseModel):
    file_id: str
    units: Units
    totals: Totals = Field(description="Totals over the whole file, ignoring pagination/filters.")
    total: int = Field(description="Number of features matching the filters.")
    limit: int
    offset: int
    items: list[MeasuredFeature]
