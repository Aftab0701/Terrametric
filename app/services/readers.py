from __future__ import annotations

import datetime as dt
import gc
import math
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import shapefile
import shapely
from pyproj import CRS
from pyproj.exceptions import CRSError
from shapely.geometry import GeometryCollection, LineString, MultiLineString, MultiPolygon, Point, Polygon
from shapely.geometry.base import BaseGeometry

from ..errors import ProcessingError
from .crs import WGS84

KML_NOISE = {
    "id",
    "timestamp",
    "begin",
    "end",
    "altitudeMode",
    "tessellate",
    "extrude",
    "visibility",
    "drawOrder",
    "icon",
}


@dataclass
class RawFeature:
    index: int
    layer: str | None
    geometry: BaseGeometry | None
    properties: dict[str, Any]
    read_error: str | None = None


@dataclass
class ParsedDataset:
    features: list[RawFeature]
    crs: CRS
    warnings: list[str] = field(default_factory=list)


def read_dataset(path: Path, fmt: str, max_features: int) -> ParsedDataset:
    if fmt == "shapefile":
        return _read_shapefile(path, max_features)
    if fmt in ("kml", "kmz"):
        return _read_kml(path, max_features)
    raise ProcessingError(f"Unsupported format: {fmt}")


def _read_shapefile(path: Path, max_features: int) -> ParsedDataset:
    warnings: list[str] = []
    reader = None
    features: list[RawFeature] = []
    try:
        reader = shapefile.Reader(str(path), encoding="utf-8")
        shape_records = reader.shapeRecords()
        if len(shape_records) > max_features:
            raise ProcessingError(f"File has more than {max_features} features; refusing to process.")

        for idx, item in enumerate(shape_records):
            geom, read_error = _parse_shape_geometry(item.shape)
            props = {k: _to_py(v) for k, v in item.record.as_dict().items()}
            features.append(
                RawFeature(
                    index=idx,
                    layer=None,
                    geometry=geom,
                    properties=props,
                    read_error=read_error,
                )
            )
    except ProcessingError:
        raise
    except Exception as exc:
        raise ProcessingError(f"Could not open shapefile: {_first_line(exc)}") from exc
    finally:
        if reader is not None:
            try:
                reader.close()
            except Exception:
                pass
        gc.collect()

    crs = _resolve_shapefile_crs(path, features, warnings)
    return ParsedDataset(features=features, crs=crs, warnings=warnings)


def _resolve_shapefile_crs(path: Path, features: list[RawFeature], warnings: list[str]) -> CRS:
    prj_path = path.with_suffix(".prj")
    if prj_path.exists():
        try:
            wkt = prj_path.read_text(encoding="utf-8", errors="replace").strip()
            if wkt:
                return CRS.from_user_input(wkt)
        except (CRSError, Exception):
            pass

    geoms = [f.geometry for f in features if f.geometry is not None and not f.geometry.is_empty]
    if geoms:
        minx, miny, maxx, maxy = shapely.bounds(shapely.GeometryCollection(geoms))
        if -180 <= minx and maxx <= 180 and -90 <= miny and maxy <= 90:
            warnings.append(
                "CRS_ASSUMED: no .prj file found; coordinates look like longitude/latitude, "
                "so EPSG:4326 was assumed."
            )
            return WGS84

    raise ProcessingError(
        "The shapefile has no CRS (.prj missing or unreadable) and its coordinates are not "
        "plausible longitude/latitude values, so a CRS cannot be safely assumed. "
        "Include the .prj file in the zip."
    )


def _parse_shape_geometry(shape: shapefile.Shape) -> tuple[BaseGeometry | None, str | None]:
    try:
        geo = shape.__geo_interface__
        if not geo or not geo.get("coordinates"):
            return None, None
        geom = shapely.geometry.shape(geo)
        geom_2d = shapely.force_2d(geom)
        return geom_2d, None
    except Exception as exc:
        return None, f"Geometry could not be decoded: {_first_line(exc)}"


def _read_kml(path: Path, max_features: int) -> ParsedDataset:
    warnings: list[str] = []
    try:
        tree = ET.parse(path)
        root = tree.getroot()
    except Exception as exc:
        raise ProcessingError(f"Could not parse KML: {_first_line(exc)}") from exc

    placemark_pairs = _extract_placemarks_with_layers(root)
    if len(placemark_pairs) > max_features:
        raise ProcessingError(f"File has more than {max_features} features; refusing to process.")

    features: list[RawFeature] = []
    for idx, (pm_elem, layer_name) in enumerate(placemark_pairs):
        geom, read_error = _extract_kml_geometry(pm_elem)
        props = _extract_kml_properties(pm_elem)
        features.append(
            RawFeature(
                index=idx,
                layer=layer_name,
                geometry=geom,
                properties=props,
                read_error=read_error,
            )
        )

    return ParsedDataset(features=features, crs=WGS84, warnings=warnings)


def _local_name(tag: str) -> str:
    return tag.split("}")[-1] if "}" in tag else tag


def _first_child(elem: ET.Element | None, name: str) -> ET.Element | None:
    if elem is None:
        return None
    for child in elem:
        if _local_name(child.tag) == name:
            return child
    return None


def _all_children(elem: ET.Element | None, name: str) -> list[ET.Element]:
    if elem is None:
        return []
    return [child for child in elem if _local_name(child.tag) == name]


def _extract_placemarks_with_layers(
    elem: ET.Element, current_layer: str | None = None
) -> list[tuple[ET.Element, str | None]]:
    items: list[tuple[ET.Element, str | None]] = []
    tag = _local_name(elem.tag)
    if tag == "Folder":
        name_elem = _first_child(elem, "name")
        current_layer = name_elem.text.strip() if name_elem is not None and name_elem.text else None
    elif tag == "Placemark":
        items.append((elem, current_layer))
        return items

    for child in elem:
        items.extend(_extract_placemarks_with_layers(child, current_layer))
    return items


def _parse_coordinates(text: str | None) -> list[tuple[float, float]]:
    if not text:
        return []
    points: list[tuple[float, float]] = []
    for chunk in text.strip().split():
        parts = chunk.split(",")
        if len(parts) >= 2:
            try:
                points.append((float(parts[0]), float(parts[1])))
            except ValueError:
                continue
    return points


def _parse_kml_geometry_element(elem: ET.Element) -> BaseGeometry | None:
    tag = _local_name(elem.tag)
    if tag == "Point":
        coord_elem = _first_child(elem, "coordinates")
        pts = _parse_coordinates(coord_elem.text if coord_elem is not None else "")
        return Point(pts[0]) if pts else None
    if tag == "LineString":
        coord_elem = _first_child(elem, "coordinates")
        pts = _parse_coordinates(coord_elem.text if coord_elem is not None else "")
        return LineString(pts) if len(pts) >= 2 else None
    if tag == "LinearRing":
        coord_elem = _first_child(elem, "coordinates")
        pts = _parse_coordinates(coord_elem.text if coord_elem is not None else "")
        return shapely.geometry.LinearRing(pts) if len(pts) >= 3 else None
    if tag == "Polygon":
        outer_elem = _first_child(elem, "outerBoundaryIs")
        outer_ring = _first_child(outer_elem, "LinearRing") if outer_elem is not None else None
        outer_coord = _first_child(outer_ring, "coordinates") if outer_ring is not None else None
        outer_pts = _parse_coordinates(outer_coord.text if outer_coord is not None else "")
        if len(outer_pts) < 3:
            return None
        holes: list[list[tuple[float, float]]] = []
        for inner_elem in _all_children(elem, "innerBoundaryIs"):
            inner_ring = _first_child(inner_elem, "LinearRing")
            inner_coord = _first_child(inner_ring, "coordinates") if inner_ring is not None else None
            inner_pts = _parse_coordinates(inner_coord.text if inner_coord is not None else "")
            if len(inner_pts) >= 3:
                holes.append(inner_pts)
        return Polygon(shell=outer_pts, holes=holes)
    if tag == "MultiGeometry":
        sub_geoms: list[BaseGeometry] = []
        for child in elem:
            child_geom = _parse_kml_geometry_element(child)
            if child_geom is not None and not child_geom.is_empty:
                sub_geoms.append(child_geom)
        if not sub_geoms:
            return None
        if all(g.geom_type == "LineString" for g in sub_geoms):
            return MultiLineString(sub_geoms)
        if all(g.geom_type == "Polygon" for g in sub_geoms):
            return MultiPolygon(sub_geoms)
        return GeometryCollection(sub_geoms)
    return None


def _extract_kml_geometry(pm_elem: ET.Element) -> tuple[BaseGeometry | None, str | None]:
    for child in pm_elem:
        tag = _local_name(child.tag)
        if tag in ("Point", "LineString", "LinearRing", "Polygon", "MultiGeometry"):
            try:
                geom = _parse_kml_geometry_element(child)
                return geom, None
            except Exception as exc:
                return None, f"Geometry could not be decoded: {_first_line(exc)}"
    return None, None


def _extract_kml_properties(pm_elem: ET.Element) -> dict[str, Any]:
    props: dict[str, Any] = {}
    name_elem = _first_child(pm_elem, "name")
    if name_elem is not None and name_elem.text:
        props["name"] = name_elem.text.strip()

    desc_elem = _first_child(pm_elem, "description")
    if desc_elem is not None and desc_elem.text:
        props["description"] = desc_elem.text.strip()

    ext_elem = _first_child(pm_elem, "ExtendedData")
    if ext_elem is not None:
        for data_elem in _all_children(ext_elem, "Data"):
            key = data_elem.attrib.get("name")
            val_elem = _first_child(data_elem, "value")
            if key and val_elem is not None and val_elem.text is not None:
                props[key] = val_elem.text.strip()
        for simple_elem in _all_children(ext_elem, "SimpleData"):
            key = simple_elem.attrib.get("name")
            if key and simple_elem.text is not None:
                props[key] = simple_elem.text.strip()

    return {k: v for k, v in props.items() if k not in KML_NOISE and v is not None}


def _to_py(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (np.datetime64,)):
        return None if np.isnat(value) else str(value)
    if isinstance(value, np.generic):
        value = value.item()
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return None
    if isinstance(value, (dt.datetime, dt.date)):
        return value.isoformat()
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def _first_line(exc: Exception) -> str:
    return (str(exc).strip().splitlines() or [type(exc).__name__])[0][:300]
