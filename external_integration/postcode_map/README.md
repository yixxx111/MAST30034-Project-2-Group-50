# Postcode socio-economic map (geospatial visualisation)

Added because everything else in `external_integration` / `member3_merchant_features`
is a table (coverage %, missingness counts, feature CSVs) — there wasn't an
actual map. This module produces one: Australia's postcodes, coloured by
socio-economic disadvantage.

## What it shows

`results/postcode_socioeconomic_map.png` — a national choropleth of
**ABS SEIFA 2021 IRSD national decile** (Index of Relative Socio-economic
Disadvantage; 1 = most disadvantaged, 10 = least disadvantaged) by postcode,
plus four zoomed insets (Sydney, Melbourne, Brisbane, Perth) because at
national scale small, high-value inner-city postcodes are invisible.

IRSD was chosen over the other SEIFA indexes (IRSAD/IER/IEO, all of which
are already in `external_postcode_features.csv`) because it's the one most
directly about disadvantage and the one most people mean by "the SEIFA
score"; swapping `METRIC` in `build_postcode_map.py` to any other SEIFA
column, or to a Census/ATO income field, reuses the same map code.

## Data sources (real, not estimated)

1. **Postcode boundaries**: ABS ASGS Edition 3, POA (2021) postal area
   digital boundaries. Fetched pre-converted-to-GeoJSON from
   [ferocia/australia-geojsons](https://github.com/ferocia/australia-geojsons)
   (`outputs/au-postcodes-Visvalingam-5.geojson`, itself derived from the
   official ABS shapefiles: POA_CODE + polygon geometry only), then
   simplified further here with `geopandas.simplify(tolerance=0.005)`
   (Douglas-Peucker) purely to keep the file small enough for git — a
   cartographic simplification of the polygon outlines, it does not touch
   which postcode a point falls in or any feature value.
2. **Socio-economic values**: this project's own
   `external_integration/results/external_postcode_features.csv` (Census +
   SEIFA + ATO, already built and validated by `external_integration/integrate.py`).

The two are joined on postcode. This is a join between two **independently
sourced** postcode lists, so coverage isn't 100% by construction — see
`results/join_coverage.csv`:

| | count |
|---|---|
| Boundary polygons (postcodes with a shape) | 2,513 |
| ...matched to a row in external_postcode_features.csv | 2,475 (98.5%) |
| Feature-table postcodes with no boundary polygon | 213 |

The unmatched boundary polygons and unmatched feature-table postcodes are
mostly explained by the same thing: PO-box-only postcodes have a
statistical (Census/SEIFA/ATO) record but no physical delivery-area
boundary, and vice versa. This is noted rather than hidden — it does not
affect the ~2,475 postcodes that are shown.

## Files

- `build_postcode_map.py` — loads both sources, joins, plots, saves
- `local_data/au_postcode_boundaries.geojson` — cached simplified boundary
  file (so this doesn't need network access to reproduce)
- `results/postcode_socioeconomic_map.png` — the map
- `results/postcode_boundaries_with_features.geojson` — boundaries + this
  project's feature values, joined, for anyone who wants to build a
  different map from the same data (e.g. in QGIS or a Design/BI tool)
- `results/join_coverage.csv` — the join-coverage numbers above

## Reproducing

```
python3 build_postcode_map.py
```

Requires `geopandas`, `pandas`, `matplotlib` (`pip install geopandas pandas matplotlib`).
