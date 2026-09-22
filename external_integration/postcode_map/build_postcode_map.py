"""
Geospatial visualisation: an actual map of Australia, postcodes colour-coded
by socio-economic disadvantage (ABS SEIFA IRSD 2021 national decile).

Requested because the existing external_integration / merchant_features
outputs are all tables (coverage %, missingness counts, feature CSVs) -- none
of them is a map. This module adds the missing map.

Inputs
------
- local_data/au_postcode_boundaries.geojson: ABS ASGS Ed.3 POA (2021) postal
  area boundaries, POA_CODE + polygon geometry only. Sourced from
  https://github.com/ferocia/australia-geojsons (their `outputs/
  au-postcodes-Visvalingam-5.geojson`, itself derived from the official ABS
  digital boundary files), then simplified further here (Douglas-Peucker,
  tolerance 0.005 deg) purely to keep the file small in git -- this is a
  cartographic simplification, it does not touch the underlying feature
  data or postcode assignment.
- ../results/external_postcode_features.csv: this project's own postcode-
  level socio-economic feature table (Census + SEIFA + ATO), already built
  by external_integration/integrate.py.

Output
------
- results/postcode_socioeconomic_map.png: national map + 4 metro insets
- results/postcode_boundaries_with_features.geojson: the boundaries joined
  to this project's feature table, for anyone who wants to make their own
  map from the same data
- results/join_coverage.csv: how many of the 2,513 postcode polygons found
  a match in the feature table (and vice versa)
"""
import json
import geopandas as gpd
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent  # external_integration/
BOUNDARIES = HERE / "local_data" / "au_postcode_boundaries.geojson"
FEATURES = ROOT / "results" / "external_postcode_features.csv"
OUT = HERE / "results"
OUT.mkdir(exist_ok=True)

METRIC = "seifa_irsd_national_decile"
METRIC_LABEL = "SEIFA IRSD national decile\n(1 = most disadvantaged, 10 = least disadvantaged)"

# --- load ---
gdf = gpd.read_file(BOUNDARIES)
gdf["postcode"] = gdf["POA_CODE"].astype(int)
feat = pd.read_csv(FEATURES)
assert feat["postcode"].is_unique

merged = gdf.merge(feat, on="postcode", how="left")

# --- coverage bookkeeping (this is a join between two independently-sourced
# postcode lists -- ABS boundary polygons vs this project's feature table --
# so a full match isn't guaranteed and shouldn't be silently assumed) ---
n_geo = len(gdf)
n_matched = merged[METRIC].notna().sum()
n_feat_unmatched_to_geo = (~feat["postcode"].isin(gdf["postcode"])).sum()
pd.DataFrame([{
    "boundary_polygons": n_geo,
    "boundary_polygons_matched_to_feature_table": int(n_matched),
    "boundary_polygons_missing_feature_data_pct": round(100 * (1 - n_matched / n_geo), 1),
    "feature_table_postcodes_with_no_boundary_polygon": int(n_feat_unmatched_to_geo),
    "note": "unmatched boundary polygons are mostly PO-box-only / non-standard "
            "postcodes with no ABS SEIFA/Census/ATO record; unmatched feature-"
            "table postcodes are mostly PO boxes that have a statistical record "
            "but no physical delivery area boundary",
}]).to_csv(OUT / "join_coverage.csv", index=False)

# --- save the joined geometry + features as this module's own geospatial output ---
keep_cols = ["postcode", "geometry", METRIC, "seifa_irsd_national_decile",
             "census_median_household_income_weekly", "ato_taxable_income_or_loss_per_reporter",
             "all_sources_matched"]
keep_cols = [c for c in dict.fromkeys(keep_cols) if c in merged.columns]
merged[keep_cols].to_file(OUT / "postcode_boundaries_with_features.geojson", driver="GeoJSON")

# --- figure: national choropleth + 4 metro insets ---
CMAP = "viridis"  # perceptually-uniform sequential ramp: this is a magnitude
                   # (decile 1-10), not a polarity around a neutral midpoint,
                   # so a diverging red/blue scale would overstate a "zero"
                   # that doesn't exist in the data -- see dataviz skill.
VMIN, VMAX = 1, 10
MISSING_COLOR = "#e0e0e0"

fig = plt.figure(figsize=(15, 11), dpi=150)
gs = fig.add_gridspec(2, 4, height_ratios=[3, 1.3], hspace=0.25, wspace=0.15)
ax_main = fig.add_subplot(gs[0, :])

merged.plot(
    column=METRIC, cmap=CMAP, vmin=VMIN, vmax=VMAX, linewidth=0.05,
    edgecolor="white", ax=ax_main, missing_kwds={"color": MISSING_COLOR, "label": "No SEIFA data"},
)
ax_main.set_xlim(112, 154)
ax_main.set_ylim(-44, -9)
ax_main.set_title(
    "Socio-economic disadvantage by postcode, Australia\n"
    "ABS SEIFA 2021 Index of Relative Socio-economic Disadvantage (IRSD)",
    fontsize=14, loc="left",
)
ax_main.set_axis_off()

sm = plt.cm.ScalarMappable(cmap=CMAP, norm=plt.Normalize(vmin=VMIN, vmax=VMAX))
sm._A = []
cbar = fig.colorbar(sm, ax=ax_main, fraction=0.025, pad=0.01, shrink=0.7)
cbar.set_label(METRIC_LABEL, fontsize=9)
missing_patch = mpatches.Patch(color=MISSING_COLOR, label="No SEIFA data for this postcode")
ax_main.legend(handles=[missing_patch], loc="lower left", frameon=False, fontsize=9)

# metro insets: national scale hides small, high-value city postcodes, so zoom in
insets = [
    ("Sydney",    151.20, -33.87, 0.55),
    ("Melbourne", 144.96, -37.81, 0.55),
    ("Brisbane",  153.03, -27.47, 0.45),
    ("Perth",     115.86, -31.95, 0.45),
]
for i, (name, lon, lat, half_span) in enumerate(insets):
    ax = fig.add_subplot(gs[1, i])
    merged.plot(
        column=METRIC, cmap=CMAP, vmin=VMIN, vmax=VMAX, linewidth=0.15,
        edgecolor="white", ax=ax, missing_kwds={"color": MISSING_COLOR},
    )
    ax.set_xlim(lon - half_span, lon + half_span)
    ax.set_ylim(lat - half_span, lat + half_span)
    ax.set_title(name, fontsize=10)
    ax.set_axis_off()

fig.text(
    0.01, 0.01,
    "Postcode boundaries: ABS ASGS Ed.3 POA (2021), via ferocia/australia-geojsons "
    "(cartographically simplified for file size). Disadvantage index: ABS SEIFA 2021 IRSD. "
    "Feature values: this project's external_integration/results/external_postcode_features.csv.",
    fontsize=7, color="#666666",
)
fig.savefig(OUT / "postcode_socioeconomic_map.png", bbox_inches="tight")
print("Saved map to", OUT / "postcode_socioeconomic_map.png")
print("Join coverage:", n_matched, "/", n_geo, "boundary polygons matched")
