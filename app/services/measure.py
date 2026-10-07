from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from typing import Any

import numpy as np
import shapely
from pyproj import CRS, Geod, Transformer
from shapely.geometry.base import BaseGeometry
from shapely.validation import explain_validity, make_valid

from ..models import MeasurementStatus
from .crs import WGS84, is_wgs84, laea_crs, local_aeqd_crs, local_tm_crs, normalise_lon

DEVIATION_WARN_PCT = 0.5


def _reproject(tr: Transformer, geom: BaseGeometry) -> BaseGeometry:
    def apply(coords: np.ndarray) -> np.ndarray:
        x, y = tr.transform(coords[:, 0], coords[:, 1])
        return np.column_stack((x, y))

    return shapely.transform(geom, apply)


class MeasurementError(Exception):
    pass


@dataclass
class Measurement:
    status: MeasurementStatus
    notes: list[str] = field(default_factory=list)
    geometry_repaired: bool = False
    area_m2: float | None = None
    perimeter_m: float | None = None
    length_m: float | None = None
    methods: dict[str, str] = field(default_factory=dict)
    geodesic: dict[str, float] = field(default_factory=dict)
    max_deviation_pct: float | None = None

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["status"] = self.status.value
        return d


@dataclass
class _Parts:
    polygons: list[BaseGeometry] = field(default_factory=list)
    lines: list[BaseGeometry] = field(default_factory=list)
    points: int = 0
    unsupported: list[str] = field(default_factory=list)


def _decompose(geom: BaseGeometry) -> _Parts:
    parts = _Parts()
    stack = [geom]
    while stack:
        g = stack.pop()
        if g is None or g.is_empty:
            continue
        t = g.geom_type
        if t == "Polygon":
            parts.polygons.append(g)
        elif t in ("LineString", "LinearRing"):
            parts.lines.append(g)
        elif t == "Point":
            parts.points += 1
        elif t in ("MultiPoint",):
            parts.points += len(g.geoms)
        elif t in ("MultiPolygon", "MultiLineString", "GeometryCollection"):
            stack.extend(g.geoms)
        else:
            parts.unsupported.append(t)
    return parts


class Measurer:
    def __init__(self, source_crs: CRS) -> None:
        self._to_wgs84 = (
            None if is_wgs84(source_crs) else Transformer.from_crs(source_crs, WGS84, always_xy=True)
        )
        self._geod = Geod(ellps="WGS84")
        self._laea: dict[tuple[int, int], Transformer] = {}
        self._dist: dict[tuple, tuple[Transformer, str]] = {}

    def measure(self, geom: BaseGeometry | None) -> Measurement:
        if geom is None or geom.is_empty:
            return Measurement(MeasurementStatus.NO_GEOMETRY, notes=["Feature has no geometry."])

        notes: list[str] = []
        repaired = False
        if not shapely.is_valid(geom):
            notes.append(f"Invalid geometry repaired before measuring: {explain_validity(geom)}.")
            geom = make_valid(geom)
            repaired = True

        parts = _decompose(geom)
        if parts.unsupported:
            notes.append(f"Ignored unsupported parts: {sorted(set(parts.unsupported))}.")
        if parts.points and (parts.polygons or parts.lines):
            notes.append(f"Ignored {parts.points} point part(s); points have no measurement.")

        if not parts.polygons and not parts.lines:
            if parts.points:
                return Measurement(
                    MeasurementStatus.NOT_APPLICABLE,
                    notes=["Points have no area or length."],
                    geometry_repaired=repaired,
                )
            return Measurement(
                MeasurementStatus.UNSUPPORTED,
                notes=notes or [f"Unsupported geometry type: {geom.geom_type}."],
                geometry_repaired=repaired,
            )

        polys_ll = [self._wgs84(p) for p in parts.polygons]
        lines_ll = [self._wgs84(l) for l in parts.lines]
        self._sanity_check(polys_ll + lines_ll, notes)

        result = Measurement(MeasurementStatus.MEASURED, notes=notes, geometry_repaired=repaired)
        deviations: list[float] = []

        if polys_ll:
            area, area_method = self._projected_area(polys_ll)
            perim, perim_method = self._projected_length(polys_ll)
            g_area, g_perim = self._geodesic_polygons(polys_ll)
            result.area_m2 = round(area, 4)
            result.perimeter_m = round(perim, 4)
            result.methods["area"] = area_method
            result.methods["perimeter"] = perim_method
            result.geodesic["area_m2"] = round(g_area, 4)
            result.geodesic["perimeter_m"] = round(g_perim, 4)
            deviations += [_pct(area, g_area), _pct(perim, g_perim)]

        if lines_ll:
            length, method = self._projected_length(lines_ll)
            g_len = self._geodesic_lines(lines_ll)
            result.length_m = round(length, 4)
            result.methods["length"] = method
            result.geodesic["length_m"] = round(g_len, 4)
            deviations.append(_pct(length, g_len))

        finite = [d for d in deviations if d is not None]
        if finite:
            result.max_deviation_pct = round(max(finite), 6)
            if result.max_deviation_pct > DEVIATION_WARN_PCT:
                result.notes.append(
                    f"Projected and geodesic results differ by {result.max_deviation_pct:.2f}%. "
                    "Typical causes: very long straight edges (consider densifying), "
                    "a geometry spanning many degrees, or a wrong source CRS."
                )
        return result

    def _wgs84(self, geom: BaseGeometry) -> BaseGeometry:
        if self._to_wgs84 is None:
            return geom
        return _reproject(self._to_wgs84, geom)

    @staticmethod
    def _sanity_check(geoms: list[BaseGeometry], notes: list[str]) -> None:
        b = shapely.bounds(geoms)
        if not np.isfinite(b).all():
            raise MeasurementError(
                "Reprojection to WGS84 produced non-finite coordinates; the declared CRS is "
                "probably wrong for this data."
            )
        if (np.abs(b[:, [1, 3]]) > 90).any() or (np.abs(b[:, [0, 2]]) > 360).any():
            raise MeasurementError(
                "Coordinates are outside the valid longitude/latitude range after reprojection; "
                "check the file's CRS."
            )
        if (b[:, 2] - b[:, 0] > 180).any():
            notes.append(
                "Feature spans more than 180 degrees of longitude; it may cross the antimeridian "
                "and results may be unreliable."
            )

    @staticmethod
    def _center(geoms: list[BaseGeometry]) -> tuple[float, float]:
        minx, miny, maxx, maxy = shapely.bounds(shapely.GeometryCollection(geoms))
        return (minx + maxx) / 2.0, (miny + maxy) / 2.0

    def _laea_for(self, lon: float, lat: float) -> tuple[Transformer, str]:
        key = (round(normalise_lon(lon)), round(lat))
        if key not in self._laea:
            self._laea[key] = Transformer.from_crs(WGS84, laea_crs(key[0], key[1]), always_xy=True)
        return self._laea[key], f"LAEA(lat_0={key[1]}, lon_0={key[0]})"

    def _distance_for(self, lon: float, lat: float) -> tuple[Transformer, str]:
        if abs(lat) > 80.0:
            key: tuple = ("aeqd", round(normalise_lon(lon)), round(lat))
            if key not in self._dist:
                crs = local_aeqd_crs(key[1], key[2])
                self._dist[key] = (
                    Transformer.from_crs(WGS84, crs, always_xy=True),
                    f"AEQD(lat_0={key[2]}, lon_0={key[1]})",
                )
        else:
            lon0 = round(normalise_lon(lon) * 2) / 2
            key = ("tm", lon0)
            if key not in self._dist:
                self._dist[key] = (
                    Transformer.from_crs(WGS84, local_tm_crs(lon0), always_xy=True),
                    f"TM(lon_0={lon0}, k0=1)",
                )
        return self._dist[key]

    def _projected_area(self, polys_ll: list[BaseGeometry]) -> tuple[float, str]:
        lon, lat = self._center(polys_ll)
        tr, label = self._laea_for(lon, lat)
        return sum(_reproject(tr, p).area for p in polys_ll), label

    def _projected_length(self, geoms_ll: list[BaseGeometry]) -> tuple[float, str]:
        lon, lat = self._center(geoms_ll)
        tr, label = self._distance_for(lon, lat)
        return sum(_reproject(tr, g).length for g in geoms_ll), label

    def _geodesic_polygons(self, polys_ll: list[BaseGeometry]) -> tuple[float, float]:
        area = perim = 0.0
        for poly in polys_ll:
            a, p = self._ring_area_perimeter(poly.exterior)
            area += a
            perim += p
            for hole in poly.interiors:
                ha, hp = self._ring_area_perimeter(hole)
                area -= ha
                perim += hp
        return area, perim

    def _ring_area_perimeter(self, ring) -> tuple[float, float]:
        xs, ys = ring.xy
        a, p = self._geod.polygon_area_perimeter(list(xs), list(ys))
        return abs(a), p

    def _geodesic_lines(self, lines_ll: list[BaseGeometry]) -> float:
        total = 0.0
        for line in lines_ll:
            xs, ys = line.xy
            total += self._geod.line_length(list(xs), list(ys))
        return total


def _pct(projected: float, reference: float) -> float | None:
    if reference == 0 or not math.isfinite(reference):
        return None
    return abs(projected - reference) / reference * 100.0
