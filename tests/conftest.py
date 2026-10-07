from __future__ import annotations

import shutil
import zipfile
from pathlib import Path

import geopandas as gpd
import pytest
import shapefile
import shapely.geometry
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app

SAMPLES = Path(__file__).resolve().parent.parent / "samples"


def _to_geojson_lists(val):
    if isinstance(val, tuple):
        return [_to_geojson_lists(x) for x in val]
    if isinstance(val, list):
        return [_to_geojson_lists(x) for x in val]
    if isinstance(val, dict):
        return {k: _to_geojson_lists(v) for k, v in val.items()}
    return val


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(database_url=f"sqlite:///{tmp_path / 'test.db'}")


@pytest.fixture
def client(settings):
    with TestClient(create_app(settings)) as c:
        yield c


@pytest.fixture
def make_client(tmp_path):
    def _make(**overrides) -> TestClient:
        s = Settings(database_url=f"sqlite:///{tmp_path / 'custom.db'}", **overrides)
        return TestClient(create_app(s))

    return _make


@pytest.fixture
def shapefile_zip(tmp_path):
    def _make(gdf: gpd.GeoDataFrame, name="data", keep_prj=True) -> Path:
        folder = tmp_path / f"{name}_dir"
        folder.mkdir(parents=True, exist_ok=True)
        shp_path = folder / f"{name}.shp"
        with shapefile.Writer(str(shp_path)) as w:
            cols = [c for c in gdf.columns if c != "geometry"]
            for col in cols:
                w.field(str(col), "C", size=50)
            for _, row in gdf.iterrows():
                geo_dict = _to_geojson_lists(shapely.geometry.mapping(row.geometry))
                w.shape(geo_dict)
                if cols:
                    w.record(*[row[c] for c in cols])
                else:
                    w.record()
        if keep_prj and gdf.crs:
            (folder / f"{name}.prj").write_text(gdf.crs.to_wkt(), encoding="utf-8")
        out = tmp_path / f"{name}.zip"
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
            for f in folder.iterdir():
                zf.write(f, f.name)
        shutil.rmtree(folder)
        return out

    return _make


def upload(client: TestClient, path: Path, content_type="application/octet-stream"):
    with open(path, "rb") as fh:
        return client.post("/api/files/", files={"file": (path.name, fh, content_type)})
