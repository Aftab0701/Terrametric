import tempfile
import zipfile
from pathlib import Path

import geopandas as gpd
import pytest
import shapefile
import shapely.geometry
from shapely.geometry import LineString, Point, box

from app.models import FileRecord, FileStatus
from tests.conftest import SAMPLES, upload


def test_kml_upload_end_to_end(client):
    r = upload(client, SAMPLES / "vijayawada_survey.kml")
    assert r.status_code == 201
    body = r.json()
    assert body["status"] == "COMPLETED"
    assert body["crs"] == "EPSG:4326"
    assert body["feature_count"] == 7
    assert body["source_format"] == "kml"
    assert body["summary"]["geometry_types"]["Polygon"] == 3

    fid = body["id"]
    assert client.get(f"/api/files/{fid}/").json()["feature_count"] == 7

    items = client.get(f"/api/files/{fid}/measurements/?include_properties=true").json()["items"]
    by_name = {i["properties"]["name"]: i for i in items}

    plain = by_name["Plain rectangle"]["measurement"]
    assert plain["status"] == "MEASURED" and plain["area"] > 9000
    assert plain["length"] is None
    assert plain["max_deviation_pct"] < 0.01

    assert by_name["Benchmark BM-7"]["measurement"]["status"] == "NOT_APPLICABLE"

    bow = by_name["Bow-tie (self-intersecting, invalid)"]["measurement"]
    assert bow["geometry_repaired"] and bow["area"] > 0

    pond = by_name["Polygon with a pond"]["measurement"]["area"]
    assert pond < 0.002 * 1e6 * 25


def test_kml_properties_are_clean_and_altitude_is_dropped(client):
    fid = upload(client, SAMPLES / "vijayawada_survey.kml").json()["id"]
    feats = client.get(f"/api/files/{fid}/features/").json()["items"]
    rect = next(f for f in feats if f["properties"].get("name") == "Plain rectangle")
    assert rect["properties"] == {"name": "Plain rectangle", "survey_no": "101"}
    assert all(len(c) == 2 for c in rect["geometry"]["coordinates"][0])
    assert rect["crs"] == "EPSG:4326" and rect["geometry_type"] == "Polygon"


def test_same_parcels_in_utm_and_web_mercator_measure_identically(client):
    a = upload(client, SAMPLES / "parcels_utm44n.zip").json()
    b = upload(client, SAMPLES / "parcels_webmercator.zip").json()
    assert (a["crs"], b["crs"]) == ("EPSG:32644", "EPSG:3857")
    assert a["summary"]["total_area_m2"] == pytest.approx(b["summary"]["total_area_m2"], rel=1e-5)
    with tempfile.TemporaryDirectory() as td:
        with zipfile.ZipFile(SAMPLES / "parcels_webmercator.zip") as zf:
            zf.extractall(td)
        shp_path = next(Path(td).glob("*.shp"))
        with shapefile.Reader(str(shp_path)) as sf:
            naive = sum(shapely.geometry.shape(r.shape.__geo_interface__).area for r in sf.shapeRecords())
    assert naive / b["summary"]["total_area_m2"] > 1.08


def test_unit_conversion_and_totals(client):
    fid = upload(client, SAMPLES / "parcels_utm44n.zip").json()["id"]
    sq = client.get(f"/api/files/{fid}/measurements/").json()
    ha = client.get(f"/api/files/{fid}/measurements/?area_unit=ha&length_unit=km").json()
    assert ha["units"] == {"area": "ha", "length": "km"}
    assert ha["totals"]["area"] == pytest.approx(sq["totals"]["area"] / 10_000, rel=1e-5)
    assert ha["items"][0]["measurement"]["perimeter"] == pytest.approx(
        sq["items"][0]["measurement"]["perimeter"] / 1000, rel=1e-5
    )
    assert client.get(f"/api/files/{fid}/measurements/?area_unit=bogus").status_code == 422


def test_pagination_and_filters(client):
    fid = upload(client, SAMPLES / "vijayawada_survey.kml").json()["id"]
    page = client.get(f"/api/files/{fid}/measurements/?limit=2&offset=1").json()
    assert page["total"] == 7 and [i["index"] for i in page["items"]] == [1, 2]
    na = client.get(f"/api/files/{fid}/measurements/?status=NOT_APPLICABLE").json()
    assert na["total"] == 1 and na["items"][0]["geometry_type"] == "Point"
    lines = client.get(f"/api/files/{fid}/measurements/?geometry_type=LineString").json()
    assert lines["total"] == 1
    assert lines["totals"]["length"] == pytest.approx(page["totals"]["length"])


def test_features_endpoint_can_omit_geometry(client):
    fid = upload(client, SAMPLES / "parcels_utm44n.zip").json()["id"]
    items = client.get(f"/api/files/{fid}/features/?include_geometry=false").json()["items"]
    assert items[0]["geometry"] is None
    assert items[0]["properties"]["parcel_id"] == "P-001"


def test_list_and_delete(client):
    fid = upload(client, SAMPLES / "parcels_utm44n.zip").json()["id"]
    assert client.get("/api/files/").json()["total"] == 1
    assert client.delete(f"/api/files/{fid}/").status_code == 204
    assert client.get(f"/api/files/{fid}/").status_code == 404
    assert client.get("/api/files/").json()["total"] == 0


def test_kmz_upload(client, tmp_path):
    kmz = tmp_path / "survey.kmz"
    with zipfile.ZipFile(kmz, "w") as zf:
        zf.write(SAMPLES / "vijayawada_survey.kml", "doc.kml")
    body = upload(client, kmz).json()
    assert body["source_format"] == "kmz" and body["feature_count"] == 7


def test_shapefile_lines_and_points(client, shapefile_zip):
    lines = gpd.GeoDataFrame({"n": [1]}, geometry=[LineString([(80.0, 16.0), (80.0, 17.0)])], crs=4326)
    body = upload(client, shapefile_zip(lines, "roads")).json()
    m = client.get(f"/api/files/{body['id']}/measurements/").json()["items"][0]["measurement"]
    assert m["length"] == pytest.approx(110_600, abs=300)

    pts = gpd.GeoDataFrame({"n": [1, 2]}, geometry=[Point(80, 16), Point(81, 17)], crs=4326)
    body = upload(client, shapefile_zip(pts, "pts")).json()
    assert body["summary"]["measurement_status"] == {"NOT_APPLICABLE": 2}
    assert body["status"] == "COMPLETED"


def test_missing_prj_with_lonlat_coordinates_assumes_4326_and_warns(client, shapefile_zip):
    gdf = gpd.GeoDataFrame({"n": [1]}, geometry=[box(80.64, 16.5, 80.65, 16.51)], crs=4326)
    body = upload(client, shapefile_zip(gdf, "noprj", keep_prj=False)).json()
    assert body["crs"] == "EPSG:4326"
    assert any(w.startswith("CRS_ASSUMED") for w in body["warnings"])


def test_missing_prj_with_projected_coordinates_is_refused(client, shapefile_zip):
    gdf = gpd.GeoDataFrame({"n": [1]}, geometry=[box(484000, 1825000, 484100, 1825080)], crs=32644)
    r = upload(client, shapefile_zip(gdf, "noprj2", keep_prj=False))
    assert r.status_code == 422
    err = r.json()["error"]
    assert err["code"] == "processing_failed" and "cannot be safely assumed" in err["message"]
    rec = client.get(f"/api/files/{err['file_id']}/").json()
    assert rec["status"] == "FAILED" and "CRS" in rec["error"]


def test_wrong_declared_crs_fails_the_feature_not_the_file(client, shapefile_zip):
    gdf = gpd.GeoDataFrame(
        {"n": [1, 2]}, geometry=[box(484000, 1825000, 484100, 1825080), box(80.64, 16.5, 80.65, 16.51)], crs=4326
    )
    body = upload(client, shapefile_zip(gdf, "liar")).json()
    assert body["status"] == "COMPLETED"
    assert body["summary"]["measurement_status"] == {"FAILED": 1, "MEASURED": 1}
    items = client.get(f"/api/files/{body['id']}/measurements/?status=FAILED").json()["items"]
    assert "CRS" in items[0]["measurement"]["notes"][0]


def test_unsupported_type_415(client, tmp_path):
    p = tmp_path / "a.geojson"
    p.write_text("{}")
    r = upload(client, p)
    assert r.status_code == 415 and r.json()["error"]["code"] == "unsupported_file_type"


def test_corrupt_zip_422_and_no_record_created(client, tmp_path):
    p = tmp_path / "bad.zip"
    p.write_bytes(b"PK\x03\x04" + b"garbage" * 50)
    r = upload(client, p)
    assert r.status_code == 422 and r.json()["error"]["code"] == "invalid_upload"
    assert client.get("/api/files/").json()["total"] == 0


def test_upload_too_large_413(make_client, tmp_path):
    client = make_client(max_upload_bytes=1024)
    p = tmp_path / "big.kml"
    p.write_text("<kml>" + "x" * 5000 + "</kml>")
    r = upload(client, p)
    assert r.status_code == 413 and r.json()["error"]["code"] == "file_too_large"


def test_junk_inside_valid_looking_shapefile_marks_file_failed(client, tmp_path):
    p = tmp_path / "junk.zip"
    with zipfile.ZipFile(p, "w") as zf:
        for ext in ("shp", "shx", "dbf"):
            zf.writestr(f"a.{ext}", b"not really a shapefile")
    r = upload(client, p)
    assert r.status_code == 422
    fid = r.json()["error"]["file_id"]
    assert client.get(f"/api/files/{fid}/").json()["status"] == "FAILED"
    assert client.get(f"/api/files/{fid}/measurements/").status_code == 409


def test_feature_cap(make_client):
    client = make_client(max_features=3)
    r = upload(client, SAMPLES / "vijayawada_survey.kml")
    assert r.status_code == 422 and "more than 3 features" in r.json()["error"]["message"]


def test_unknown_id_404_everywhere(client):
    for path in ("", "features/", "measurements/"):
        r = client.get(f"/api/files/nope/{path}")
        assert r.status_code == 404 and r.json()["error"]["code"] == "not_found"


def test_results_not_served_while_processing(client, settings):
    with client.app.state.session_factory() as s:
        s.add(FileRecord(id="inflight", filename="x.kml", source_format="kml",
                         status=FileStatus.PROCESSING.value, warnings=[]))
        s.commit()
    r = client.get("/api/files/inflight/measurements/")
    assert r.status_code == 409 and r.json()["error"]["status"] == "PROCESSING"
    assert client.get("/api/files/inflight/").json()["status"] == "PROCESSING"


def test_filename_is_sanitised(client, tmp_path):
    with open(SAMPLES / "vijayawada_survey.kml", "rb") as fh:
        r = client.post("/api/files/", files={"file": ("../../etc/passwd.kml", fh, "application/xml")})
    assert r.json()["filename"] == "passwd.kml"
