# Terrametric

A FastAPI service that accepts a Shapefile (.zip) or KML/KMZ, extracts every feature, and returns high-accuracy area and length measurements.

Every feature measurement is computed in a locally chosen projected CRS rather than raw degrees, and cross-checked against an independent geodesic calculation on the WGS84 ellipsoid.

Example curl execution:

```bash
curl -F "file=@samples/parcels_webmercator.zip" localhost:8000/api/files/
```

Naive planar calculations in Web Mercator or raw degrees produce distorted numbers. Terrametric dynamically projects coordinates to local equal-area and conformal coordinate reference systems to guarantee millimeter-level precision.

## Setup

Python 3.11 or higher is required.

Run the following commands to set up the environment:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:create_app --factory --reload
```

Interactive OpenAPI documentation is available at `http://localhost:8000/docs`.

To test with the bundled sample datasets:

```bash
curl -F "file=@samples/vijayawada_survey.kml" localhost:8000/api/files/
curl -F "file=@samples/parcels_utm44n.zip" localhost:8000/api/files/
```

To run the automated test suite:

```bash
pip install -r requirements-dev.txt
pytest
```

Configuration environment variables:

| Variable | Default | Meaning |
|---|---|---|
| `TERRAMETRIC_DATABASE_URL` | `sqlite:///./terrametric.db` | Any SQLAlchemy database URL |
| `TERRAMETRIC_MAX_UPLOAD_MB` | `50` | Maximum request body upload size in megabytes |
| `TERRAMETRIC_MAX_UNZIPPED_MB` | `250` | Total uncompressed archive size limit in megabytes |
| `TERRAMETRIC_MAX_ZIP_MEMBERS` | `200` | Maximum number of files permitted in an archive |
| `TERRAMETRIC_MAX_FEATURES` | `100000` | Maximum feature count allowed per file |

A Dockerfile is provided for containerized deployments:

```bash
docker build -t terrametric .
docker run -p 8000:8000 terrametric
```

Sample files can be regenerated using:

```bash
python scripts/make_samples.py
```

## API

The service exposes the following endpoints:

| Method | Path | Description |
|---|---|---|
| POST | `/api/files/` | Upload and process a geospatial archive (.zip Shapefile, .kml, .kmz) |
| GET | `/api/files/{id}/` | Retrieve metadata, status, and summary of an uploaded file |
| GET | `/api/files/{id}/measurements/` | Retrieve calculated measurements for each feature |
| GET | `/api/files/{id}/features/` | Retrieve extracted geometries, CRS, and attribute properties |
| GET | `/api/files/` | List all uploaded files with pagination |
| DELETE | `/api/files/{id}/` | Delete a file record and all its associated features |
| GET | `/health` | Health check endpoint |

### POST /api/files/

Accepts multipart form-data with the `file` field containing a `.zip` Shapefile or `.kml`/`.kmz`.

Response example (`201 Created`):

```json
{
  "id": "bd8b9cf69b60",
  "filename": "vijayawada_survey.kml",
  "source_format": "kml",
  "status": "COMPLETED",
  "crs": "EPSG:4326",
  "feature_count": 7,
  "warnings": [],
  "summary": {
    "geometry_types": {
      "GeometryCollection": 1,
      "Polygon": 3,
      "LineString": 1,
      "MultiLineString": 1,
      "Point": 1
    },
    "measurement_status": {
      "MEASURED": 6,
      "NOT_APPLICABLE": 1
    },
    "total_area_m2": 84357.1425,
    "total_perimeter_m": 2611.4782,
    "total_length_m": 2855.5048,
    "max_deviation_pct": 0.000321
  },
  "error": null,
  "created_at": "2026-10-07T06:29:08.117797Z",
  "completed_at": "2026-10-07T06:29:08.133100Z"
}
```

### GET /api/files/{id}/

Returns the file record matching the structure above. The `status` field indicates `PROCESSING`, `COMPLETED`, or `FAILED`.

### GET /api/files/{id}/measurements/

Query parameters:

1. `area_unit`: Output unit for polygon area (`m2`, `km2`, `ha`, `acre`, `ft2`, `mi2`). Default is `m2`.
2. `length_unit`: Output unit for line length and polygon perimeter (`m`, `km`, `ft`, `mi`). Default is `m`.
3. `status`: Filter by feature measurement status (`MEASURED`, `NOT_APPLICABLE`, `NO_GEOMETRY`, `UNSUPPORTED`, `FAILED`).
4. `geometry_type`: Filter by geometry type (`Polygon`, `LineString`, `Point`, etc.).
5. `include_properties`: Boolean flag indicating whether attribute properties should be included in the items.
6. `limit`: Page size limit between 1 and 1000.
7. `offset`: Pagination offset.

Response example:

```json
{
  "file_id": "bd8b9cf69b60",
  "units": {
    "area": "m2",
    "length": "m"
  },
  "totals": {
    "area": 84357.1425,
    "perimeter": 2611.4782,
    "length": 2855.5048
  },
  "total": 7,
  "limit": 100,
  "offset": 0,
  "items": [
    {
      "index": 2,
      "layer": "Parcels",
      "geometry_type": "Polygon",
      "crs": "EPSG:4326",
      "properties": {
        "survey_no": "102",
        "name": "Polygon with a pond"
      },
      "measurement": {
        "status": "MEASURED",
        "area": 45369.1328,
        "perimeter": 1043.6551,
        "length": null,
        "geometry_repaired": false,
        "methods": {
          "area": "LAEA(lat_0=17, lon_0=81)",
          "perimeter": "TM(lon_0=80.5, k0=1)"
        },
        "geodesic_check": {
          "area": 45369.1328,
          "perimeter": 1043.6521,
          "length": null
        },
        "max_deviation_pct": 0.000288,
        "notes": []
      }
    },
    {
      "index": 6,
      "layer": "Landmarks",
      "geometry_type": "Point",
      "crs": "EPSG:4326",
      "measurement": {
        "status": "NOT_APPLICABLE",
        "area": null,
        "perimeter": null,
        "length": null,
        "notes": [
          "Points have no area or length."
        ]
      }
    }
  ]
}
```

Measurement status values:

1. `MEASURED`: Area, perimeter, or length was successfully calculated.
2. `NOT_APPLICABLE`: Points or multipoints where measurement is not applicable by definition.
3. `NO_GEOMETRY`: Null or empty geometry was present in the source record.
4. `UNSUPPORTED`: Geometry type could not be decoded.
5. `FAILED`: Calculation failed due to invalid coordinates or reprojection issues, with reasons documented in `notes`.

### GET /api/files/{id}/features/

Returns extracted geometries in GeoJSON structure in their native source CRS, along with properties and layer information. Supports `include_geometry=false` for lightweight metadata inspection.

### Error Handling

Standard error payload returned across all endpoints:

```json
{
  "error": {
    "code": "unsupported_file_type",
    "message": "Unsupported file type '.geojson'. Upload a .zip containing a Shapefile, or a .kml / .kmz file."
  }
}
```

Status codes and error codes:

| HTTP Status | Error Code | Trigger Condition |
|---|---|---|
| 413 | `file_too_large` | Uploaded payload or uncompressed archive exceeds size threshold |
| 415 | `unsupported_file_type` | File extension is not `.zip`, `.kml`, or `.kmz` |
| 422 | `invalid_upload` | Corrupt archive, missing required shapefile sidecars, or multiple shapefiles in one zip |
| 422 | `processing_failed` | Geospatial dataset contents could not be read or parsed |
| 404 | `not_found` | File record identifier does not exist |
| 409 | `file_not_ready` | Measurements requested before background or sync processing reached `COMPLETED` |

## Architecture

Project structure:

```
app/
  main.py            FastAPI application factory and error handlers
  api/files.py       API routes for upload, file query, measurements, and features
  models.py          SQLAlchemy database models for FileRecord and FeatureRecord
  schemas.py         Pydantic validation schemas and response serialization
  errors.py          Domain exception hierarchy and standard error mapping
  services/
    ingest.py        Upload streaming, size protection, zip validation, staging
    readers.py       Format readers for Shapefiles and KML documents
    crs.py           CRS identification and local projection generation
    measure.py       Measurement engine with local projection and geodesic verification
    pipeline.py      Processing orchestration, batch persistence, and summarization
    units.py         Unit conversion utilities
tests/               Automated test suite covering API, ingestion, and measurement math
```

### File Processing Flow

1. Upload Staging: Request body is streamed to disk in an isolated temporary directory, enforcing upload size caps. Archives are inspected to prevent zip-slip and zip-bomb attacks.
2. Format Reading: Shapefiles are read via `pyshp` along with their `.dbf` attributes and `.prj` projection files. KML files are parsed into XML tree representations, preserving folder layer names and extended attributes.
3. 2D Coordinate Normalization: Dimensions with Z coordinates or elevation values are flattened to 2D representations on the surface.
4. Validation and Repair: Self-intersecting or invalid polygons are repaired using `make_valid` and tagged with `geometry_repaired = true`.
5. Feature Isolation: Each feature is processed independently. A single invalid geometry does not fail the entire file.
6. Persistence: Processed features and measurements are bulk-inserted in batches to ensure high throughput.

### Measurement Calculation Flow

For each feature in `services/measure.py`:

1. Geometry Decomposition: Multi-geometries and GeometryCollections are decomposed into constituent parts. Points are filtered out with appropriate notices.
2. WGS84 Normalization: Coordinates are projected to WGS84 coordinates if not already in EPSG:4326.
3. Coordinate Sanity Validation: Out-of-bounds coordinates (such as UTM coordinates erroneously tagged as EPSG:4326) are caught early and marked as `FAILED`.
4. Projected Measurement:
   For polygon area: Coordinates are transformed to a local Lambert Azimuthal Equal-Area (LAEA) projection centered on the feature bounding box.
   For line length and polygon perimeter: Coordinates are transformed to a local Transverse Mercator projection with scale factor k0 = 1.0 centered on the feature meridian.
5. Independent Geodesic Verification: Area and perimeter are independently calculated on the WGS84 ellipsoid using `pyproj.Geod`. Any discrepancies exceeding 0.5% are flagged in the feature notes.

### CRS Handling Strategy

1. Calculation in degrees is strictly prevented. All planar calculations occur in meters.
2. KML files are treated as EPSG:4326 in accordance with the OGC KML standard specification.
3. Shapefile projections are extracted directly from the accompanying `.prj` file.
4. When a Shapefile lacks a `.prj` file, coordinates within the -180 to 180 longitude and -90 to 90 latitude bounds are assumed to be EPSG:4326 with an explicit `CRS_ASSUMED` warning. Shapefiles lacking a `.prj` with coordinates exceeding geographic bounds are rejected with HTTP 422.
5. Local projection parameters are generated dynamically per feature based on geographic centroid coordinates, ensuring distortion remains below 0.001%.

## Design Decisions

1. Framework Selection: FastAPI was selected over Django to provide high-performance asynchronous request handling, native Pydantic schema validation, and automatic OpenAPI generation without requiring heavy ORM overhead.
2. Geospatial Library Strategy: Pyproj and Shapely are used for geometric operations and mathematical transformations. For file parsing, native pure-Python drivers (`pyshp` and XML tree parsing) are used, avoiding cumbersome external C-library dependencies while ensuring fast, reliable execution across any host platform.
3. Dual-Method Measurement: Equal-area projections are used for area, true-scale conformal projections for length, and ellipsoidal geodesic calculations for independent verification. This guarantees self-auditing numbers that catch CRS misconfigurations.
4. Feature-Level Fault Isolation: Failures in individual geometries are recorded at the feature level rather than rejecting the entire upload.
5. Database Storage: SQLite with JSON column support is used for lightweight deployment, with an architecture that allows direct switching to PostgreSQL or PostGIS through SQLAlchemy connection configuration.

## What I Learned

1. Standard UTM projections introduce a systematic 0.04% scale compression at the central meridian due to their k0 = 0.9996 scale factor. Implementing a local Transverse Mercator with k0 = 1.0 eliminated this distortion and matched ellipsoidal ground truth within 0.0003%.
2. Naive area calculations on Web Mercator (EPSG:3857) datasets yield errors between 8% and 50% depending on latitude, highlighting the necessity of proper local projections.
3. Robust input validation and boundary protections (such as uncompressed byte limits and fixed destination extraction names) are critical when ingesting untrusted archive files.

## Future Scope

1. Asynchronous task queue integration using Celery or Redis Queue with `202 Accepted` job polling for datasets exceeding 100,000 features.
2. PostGIS spatial index integration to enable bounding box queries and spatial intersections.
3. Client-selectable calculation strategies allowing geodesic primary calculations or geodesic line densification.
4. Additional geospatial format support including GeoJSON and GeoPackage.
5. Antimeridian polygon unwrapping for geometries crossing the 180-degree meridian.
