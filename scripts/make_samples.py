import shutil
import zipfile
from pathlib import Path

import shapefile
import shapely.geometry
from pyproj import CRS, Transformer
from shapely.geometry import Polygon, box

OUT = Path(__file__).resolve().parent.parent / "samples"
OUT.mkdir(exist_ok=True)


def _to_geojson_lists(val):
    if isinstance(val, tuple):
        return [_to_geojson_lists(x) for x in val]
    if isinstance(val, list):
        return [_to_geojson_lists(x) for x in val]
    if isinstance(val, dict):
        return {k: _to_geojson_lists(v) for k, v in val.items()}
    return val


def write_shapefile_zip(folder_name, zip_name, geoms, records, crs_epsg):
    tmp_dir = OUT / folder_name
    tmp_dir.mkdir(exist_ok=True)
    shp_path = tmp_dir / f"{folder_name}.shp"

    with shapefile.Writer(str(shp_path)) as w:
        cols = list(records[0].keys())
        for col in cols:
            w.field(col, "C", size=50)
        for g, r in zip(geoms, records):
            w.shape(_to_geojson_lists(shapely.geometry.mapping(g)))
            w.record(*[r[c] for c in cols])

    crs_obj = CRS.from_epsg(crs_epsg)
    (tmp_dir / f"{folder_name}.prj").write_text(crs_obj.to_wkt(), encoding="utf-8")

    zip_path = OUT / f"{zip_name}.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in tmp_dir.iterdir():
            zf.write(f, f.name)
    shutil.rmtree(tmp_dir)


x0, y0 = 484_000, 1_825_000
utm_geoms = [
    box(x0, y0, x0 + 100, y0 + 80),
    box(x0 + 150, y0, x0 + 450, y0 + 200),
    Polygon(
        [(x0, y0 + 150), (x0 + 200, y0 + 150), (x0 + 200, y0 + 350), (x0, y0 + 350)],
        holes=[[(x0 + 80, y0 + 230), (x0 + 120, y0 + 230), (x0 + 120, y0 + 270), (x0 + 80, y0 + 270)]],
    ),
    Polygon([(x0 + 250, y0 + 250), (x0 + 400, y0 + 270), (x0 + 380, y0 + 380), (x0 + 240, y0 + 360)]),
]
attributes = [
    {"parcel_id": "P-001", "owner": "Rao", "land_use": "residential"},
    {"parcel_id": "P-002", "owner": "Lakshmi", "land_use": "agricultural"},
    {"parcel_id": "P-003", "owner": "Municipal", "land_use": "park"},
    {"parcel_id": "P-004", "owner": "Reddy", "land_use": "commercial"},
]

write_shapefile_zip("parcels_utm44n", "parcels_utm44n", utm_geoms, attributes, 32644)

tr = Transformer.from_crs(CRS.from_epsg(32644), CRS.from_epsg(3857), always_xy=True)
merc_geoms = [shapely.ops.transform(tr.transform, g) for g in utm_geoms]
write_shapefile_zip("parcels_webmercator", "parcels_webmercator", merc_geoms, attributes, 3857)

kml = """<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2">
<Document>
  <name>Vijayawada demo survey</name>

  <Folder><name>Parcels</name>
    <Placemark><name>Plain rectangle</name>
      <ExtendedData><Data name="survey_no"><value>101</value></Data></ExtendedData>
      <Polygon><outerBoundaryIs><LinearRing><coordinates>
        80.6400,16.5000,31 80.6410,16.5000,31 80.6410,16.5008,31 80.6400,16.5008,31 80.6400,16.5000,31
      </coordinates></LinearRing></outerBoundaryIs></Polygon>
    </Placemark>
    <Placemark><name>Polygon with a pond</name>
      <ExtendedData><Data name="survey_no"><value>102</value></Data></ExtendedData>
      <Polygon>
        <outerBoundaryIs><LinearRing><coordinates>
          80.6420,16.5000 80.6440,16.5000 80.6440,16.5020 80.6420,16.5020 80.6420,16.5000
        </coordinates></LinearRing></outerBoundaryIs>
        <innerBoundaryIs><LinearRing><coordinates>
          80.6428,16.5008 80.6432,16.5008 80.6432,16.5012 80.6428,16.5012 80.6428,16.5008
        </coordinates></LinearRing></innerBoundaryIs>
      </Polygon>
    </Placemark>
    <Placemark><name>Bow-tie (self-intersecting, invalid)</name>
      <ExtendedData><Data name="survey_no"><value>103</value></Data></ExtendedData>
      <Polygon><outerBoundaryIs><LinearRing><coordinates>
        80.6450,16.5000 80.6460,16.5010 80.6460,16.5000 80.6450,16.5010 80.6450,16.5000
      </coordinates></LinearRing></outerBoundaryIs></Polygon>
    </Placemark>
  </Folder>

  <Folder><name>Roads</name>
    <Placemark><name>Canal road</name>
      <LineString><coordinates>80.6380,16.4990 80.6420,16.5030 80.6480,16.5035</coordinates></LineString>
    </Placemark>
    <Placemark><name>Split road (MultiGeometry)</name>
      <MultiGeometry>
        <LineString><coordinates>80.6300,16.4900 80.6350,16.4950</coordinates></LineString>
        <LineString><coordinates>80.6360,16.4960 80.6400,16.5000</coordinates></LineString>
      </MultiGeometry>
    </Placemark>
  </Folder>

  <Folder><name>Landmarks</name>
    <Placemark><name>Benchmark BM-7</name><Point><coordinates>80.6405,16.5004,31</coordinates></Point></Placemark>
  </Folder>

  <Placemark><name>Mixed geometry at document level</name>
    <MultiGeometry>
      <Point><coordinates>80.6500,16.5100</coordinates></Point>
      <LineString><coordinates>80.6500,16.5100 80.6520,16.5100</coordinates></LineString>
      <Polygon><outerBoundaryIs><LinearRing><coordinates>
        80.6500,16.5100 80.6520,16.5100 80.6520,16.5110 80.6500,16.5110 80.6500,16.5100
      </coordinates></LinearRing></outerBoundaryIs></Polygon>
    </MultiGeometry>
  </Placemark>
</Document>
</kml>
"""
(OUT / "vijayawada_survey.kml").write_text(kml, encoding="utf-8")
print("wrote", sorted(p.name for p in OUT.iterdir()))
