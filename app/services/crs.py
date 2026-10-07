from __future__ import annotations

from pyproj import CRS

WGS84 = CRS.from_epsg(4326)


def describe_crs(crs: CRS | None) -> str | None:
    if crs is None:
        return None
    epsg = crs.to_epsg(min_confidence=70)
    if epsg:
        return f"EPSG:{epsg}"
    auth = crs.to_authority(min_confidence=70)
    if auth:
        return f"{auth[0]}:{auth[1]}"
    return crs.name or "UNKNOWN"


def is_wgs84(crs: CRS) -> bool:
    return crs.equals(WGS84, ignore_axis_order=True)


def normalise_lon(lon: float) -> float:
    return ((lon + 180.0) % 360.0) - 180.0


def local_tm_crs(lon0: float) -> CRS:
    return CRS.from_proj4(
        f"+proj=tmerc +lat_0=0 +lon_0={normalise_lon(lon0):.4f} +k=1 "
        "+x_0=0 +y_0=0 +datum=WGS84 +units=m +no_defs"
    )


def local_aeqd_crs(lon0: float, lat0: float) -> CRS:
    return CRS.from_proj4(
        f"+proj=aeqd +lat_0={lat0:.4f} +lon_0={normalise_lon(lon0):.4f} "
        "+x_0=0 +y_0=0 +datum=WGS84 +units=m +no_defs"
    )


def laea_crs(lon0: float, lat0: float) -> CRS:
    return CRS.from_proj4(
        f"+proj=laea +lat_0={lat0:.4f} +lon_0={normalise_lon(lon0):.4f} "
        "+x_0=0 +y_0=0 +datum=WGS84 +units=m +no_defs"
    )
