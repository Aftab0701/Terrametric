import math

import pytest
from pyproj import CRS, Geod, Transformer
from shapely.geometry import GeometryCollection, LineString, MultiPolygon, Point, Polygon, box
from shapely.ops import transform

from app.models import MeasurementStatus as S
from app.services.measure import MeasurementError, Measurer

WGS84 = CRS.from_epsg(4326)
GEOD = Geod(ellps="WGS84")
R_AUTHALIC = 6_371_007.2


def sphere_box_area(lon1, lat1, lon2, lat2) -> float:
    return R_AUTHALIC**2 * math.radians(lon2 - lon1) * (
        math.sin(math.radians(lat2)) - math.sin(math.radians(lat1))
    )


@pytest.fixture
def m() -> Measurer:
    return Measurer(WGS84)


@pytest.mark.parametrize(
    "bounds",
    [
        (80.64, 16.50, 80.65, 16.51),
        (0.0, 0.0, 0.5, 0.5),
        (-70.2, -33.5, -70.0, -33.3),
        (10.0, 69.8, 10.4, 70.0),
        (77.0, 8.0, 79.0, 10.0),
    ],
)
def test_polygon_area_matches_oracles(m, bounds):
    r = m.measure(box(*bounds))
    assert r.status is S.MEASURED
    poly = box(*bounds)
    geod_area, _ = GEOD.geometry_area_perimeter(poly)
    assert r.area_m2 == pytest.approx(abs(geod_area), rel=2e-3)
    assert r.area_m2 == pytest.approx(sphere_box_area(*bounds), rel=1.2e-2)


def test_small_parcel_area_is_accurate_to_a_millionth(m):
    r = m.measure(box(80.64, 16.50, 80.65, 16.51))
    assert r.area_m2 == pytest.approx(r.geodesic["area_m2"], rel=1e-6)


def test_never_measures_in_degrees(m):
    poly = box(80.64, 16.50, 80.65, 16.51)
    assert poly.area == pytest.approx(1e-4)
    assert m.measure(poly).area_m2 > 1e6


def test_polygon_hole_is_subtracted(m):
    outer = [(80.64, 16.50), (80.65, 16.50), (80.65, 16.51), (80.64, 16.51)]
    hole = [(80.644, 16.504), (80.646, 16.504), (80.646, 16.506), (80.644, 16.506)]
    full = m.measure(Polygon(outer))
    holed = m.measure(Polygon(outer, [hole]))
    pond = m.measure(Polygon(hole))
    assert holed.area_m2 == pytest.approx(full.area_m2 - pond.area_m2, rel=1e-9)
    assert holed.perimeter_m == pytest.approx(full.perimeter_m + pond.perimeter_m, rel=1e-9)
    assert holed.max_deviation_pct < 0.01


def test_multipolygon_is_sum_of_parts(m):
    a, b = box(80.64, 16.50, 80.65, 16.51), box(80.70, 16.50, 80.71, 16.51)
    total = m.measure(MultiPolygon([a, b]))
    assert total.area_m2 == pytest.approx(m.measure(a).area_m2 + m.measure(b).area_m2, rel=1e-6)


def test_line_length_matches_geodesic(m):
    line = LineString([(80.0, 16.0), (80.3, 16.4), (80.9, 16.2)])
    r = m.measure(line)
    lons, lats = zip(*line.coords)
    assert r.length_m == pytest.approx(GEOD.line_length(lons, lats), rel=1e-4)
    assert r.area_m2 is None and r.perimeter_m is None


def test_one_degree_of_meridian_is_about_110_6_km(m):
    r = m.measure(LineString([(80.0, 16.0), (80.0, 17.0)]))
    assert r.length_m == pytest.approx(110_600, abs=300)


def test_polar_line_uses_azimuthal_equidistant(m):
    r = m.measure(LineString([(10, 88), (60, 88.5)]))
    assert r.methods["length"].startswith("AEQD")
    assert r.length_m == pytest.approx(r.geodesic["length_m"], rel=1e-4)


def test_points_are_not_applicable_not_errors(m):
    r = m.measure(Point(80.6, 16.5))
    assert r.status is S.NOT_APPLICABLE and r.area_m2 is None and r.length_m is None


def test_none_and_empty_geometry(m):
    assert m.measure(None).status is S.NO_GEOMETRY
    assert m.measure(Polygon()).status is S.NO_GEOMETRY


def test_invalid_bowtie_is_repaired_and_flagged(m):
    bowtie = Polygon([(80.645, 16.500), (80.646, 16.501), (80.646, 16.500), (80.645, 16.501)])
    assert not bowtie.is_valid
    r = m.measure(bowtie)
    assert r.status is S.MEASURED and r.geometry_repaired
    assert r.area_m2 > 0
    assert any("repaired" in n for n in r.notes)


def test_geometry_collection_measures_what_it_can(m):
    gc = GeometryCollection(
        [Point(80.65, 16.51), LineString([(80.65, 16.51), (80.652, 16.51)]), box(80.65, 16.51, 80.652, 16.511)]
    )
    r = m.measure(gc)
    assert r.status is S.MEASURED
    assert r.area_m2 > 0 and r.length_m > 0
    assert any("point" in n.lower() for n in r.notes)


@pytest.mark.parametrize("epsg", [3857, 32644, 7755])
def test_result_is_independent_of_source_crs(epsg):
    parcel_ll = box(80.640, 16.500, 80.650, 16.510)
    expected = Measurer(WGS84).measure(parcel_ll)
    tr = Transformer.from_crs(WGS84, CRS.from_epsg(epsg), always_xy=True)
    projected = transform(tr.transform, parcel_ll)
    got = Measurer(CRS.from_epsg(epsg)).measure(projected)
    assert got.area_m2 == pytest.approx(expected.area_m2, rel=1e-4)
    assert got.perimeter_m == pytest.approx(expected.perimeter_m, rel=1e-4)


def test_web_mercator_naive_area_is_wrong_ours_is_not():
    tr = Transformer.from_crs(WGS84, CRS.from_epsg(3857), always_xy=True)
    merc = transform(tr.transform, box(80.640, 16.500, 80.650, 16.510))
    truth = Measurer(WGS84).measure(box(80.640, 16.500, 80.650, 16.510)).area_m2
    naive = merc.area
    assert 1.085 < naive / truth < 1.10
    assert Measurer(CRS.from_epsg(3857)).measure(merc).area_m2 == pytest.approx(truth, rel=1e-4)


def test_wrong_crs_is_detected_not_silently_measured():
    with pytest.raises(MeasurementError, match="outside the valid"):
        Measurer(WGS84).measure(box(484_000, 1_825_000, 484_100, 1_825_080))


def test_long_edges_raise_a_deviation_note(m):
    r = m.measure(Polygon([(0, 40), (40, 40), (40, 50), (0, 50)]))
    assert r.max_deviation_pct > 0.5
    assert any("differ" in n for n in r.notes)
