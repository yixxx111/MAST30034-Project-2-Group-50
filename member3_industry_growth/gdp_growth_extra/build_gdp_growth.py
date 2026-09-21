"""
Extra task (not part of the four assigned pipeline modules):
FY2021-22 and FY2022-23 GDP growth rates for the project's 25 merchant
categories, presented as a chart.

Data source
-----------
Real ABS "Australian Industry" release, Industry Value Added (IVA) by
ANZSIC division, current prices ($m), for FY2020-21 -> FY2021-22 and
FY2021-22 -> FY2022-23:
  https://www.abs.gov.au/statistics/industry/industry-overview/australian-industry/2021-22
  https://www.abs.gov.au/statistics/industry/industry-overview/australian-industry/2022-23

ABS publishes IVA at the ANZSIC DIVISION level (~18-19 divisions), which is
much coarser than this project's 25 informal merchant categories. There is
no official ABS series at the "florists supplies" / "shoe shops" level of
granularity. So each of the 25 categories below is mapped, as a judgement
call (same approach already used and documented for the 5 industry_growth
groups), to the single ANZSIC division whose retail/service activity is
the closest real-world match, and that division's official YoY growth rate
is used as a stand-in for the category. This is an approximation, not a
category-specific measurement -- most of these categories are specialty
retail shops, so most of them map to the single "Retail trade" division,
which is why many categories in the resulting chart carry identical growth
figures. This limitation is stated here rather than papered over.
"""
import pandas as pd
import matplotlib.pyplot as plt

# --- ABS Industry Value Added by division, current prices, % change YoY ---
# FY21_22 = growth from 2020-21 to 2021-22 ; FY22_23 = growth from 2021-22 to 2022-23
division_growth = {
    "Agriculture, forestry and fishing":                     {"FY21_22": 25.5, "FY22_23": 8.8},
    "Mining":                                                {"FY21_22": 29.5, "FY22_23": 23.2},
    "Manufacturing":                                         {"FY21_22": 15.2, "FY22_23": 7.7},
    "Electricity, gas, water and waste services":            {"FY21_22": -1.6, "FY22_23": 6.9},
    "Construction":                                          {"FY21_22": 9.1,  "FY22_23": 14.8},
    "Wholesale trade":                                       {"FY21_22": 19.9, "FY22_23": 16.7},
    "Retail trade":                                          {"FY21_22": 12.4, "FY22_23": 14.4},
    "Accommodation and food services":                       {"FY21_22": 13.9, "FY22_23": 31.3},
    "Transport, postal and warehousing":                     {"FY21_22": 15.0, "FY22_23": 15.3},
    "Information media and telecommunications":              {"FY21_22": 13.9, "FY22_23": 6.2},
    "Rental, hiring and real estate services":                {"FY21_22": 15.4, "FY22_23": 3.4},
    "Professional, scientific and technical services":       {"FY21_22": 14.3, "FY22_23": 12.5},
    "Administrative and support services":                   {"FY21_22": 16.4, "FY22_23": 15.9},
    "Health care and social assistance (private)":           {"FY21_22": 12.5, "FY22_23": 9.7},
    "Arts and recreation services":                          {"FY21_22": 25.0, "FY22_23": 27.7},
    "Other services":                                        {"FY21_22": 17.1, "FY22_23": 17.6},
}
# Source: ABS Australian Industry 2021-22 & 2022-23 releases, Table "Employment
# and IVA data and movements", current-price IVA, division level.

# --- The project's 25 merchant categories, mapped to an industry_group (existing)
#     and to the single closest-matching ABS ANZSIC division (new, this task) ---
category_map = [
    ("antique shops - sales, repairs, and restoration services", "Art, Gifts, Jewellery & Fashion", "Retail trade"),
    ("art dealers and galleries", "Art, Gifts, Jewellery & Fashion", "Retail trade"),
    ("artist supply and craft shops", "Creative, Books & Leisure", "Retail trade"),
    ("bicycle shops - sales and service", "Mobility, Health & Specialist Services", "Retail trade"),
    ("books, periodicals, and newspapers", "Creative, Books & Leisure", "Retail trade"),
    ("cable, satellite, and other pay television and radio services", "Digital, Technology & Communications", "Information media and telecommunications"),
    ("computer programming , data processing, and integrated systems design services", "Digital, Technology & Communications", "Professional, scientific and technical services"),
    ("computers, computer peripheral equipment, and software", "Digital, Technology & Communications", "Retail trade"),
    ("digital goods: books, movies, music", "Digital, Technology & Communications", "Information media and telecommunications"),
    ("equipment, tool, furniture, and appliance rent al and leasing", "Home, Garden & Living", "Rental, hiring and real estate services"),
    ("florists supplies, nursery stock, and flowers", "Home, Garden & Living", "Retail trade"),
    ("furniture, home furnishings and equipment shops, and manufacturers, except appliances", "Home, Garden & Living", "Retail trade"),
    ("gift, card, novelty, and souvenir shops", "Art, Gifts, Jewellery & Fashion", "Retail trade"),
    ("health and beauty spas", "Mobility, Health & Specialist Services", "Other services"),
    ("hobby, toy and game shops", "Creative, Books & Leisure", "Retail trade"),
    ("jewelry, watch, clock, and silverware shops", "Art, Gifts, Jewellery & Fashion", "Retail trade"),
    ("lawn and garden supply outlets, including nurseries", "Home, Garden & Living", "Retail trade"),
    ("motor vehicle supplies and new parts", "Mobility, Health & Specialist Services", "Retail trade"),
    ("music shops - musical instruments, pianos, and sheet music", "Creative, Books & Leisure", "Retail trade"),
    ("opticians, optical goods, and eyeglasses", "Mobility, Health & Specialist Services", "Retail trade"),
    ("shoe shops", "Art, Gifts, Jewellery & Fashion", "Retail trade"),
    ("stationery, office supplies and printing and writing paper", "Creative, Books & Leisure", "Retail trade"),
    ("telecom", "Digital, Technology & Communications", "Information media and telecommunications"),
    ("tent and awning shops", "Home, Garden & Living", "Retail trade"),
    ("watch, clock, and jewelry repair shops", "Mobility, Health & Specialist Services", "Other services"),
]

rows = []
for cat, group, division in category_map:
    g = division_growth[division]
    rows.append({
        "merchant_category": cat,
        "industry_group": group,
        "matched_anzsic_division": division,
        "gdp_growth_fy21_22_pct": g["FY21_22"],
        "gdp_growth_fy22_23_pct": g["FY22_23"],
    })
df = pd.DataFrame(rows)
assert len(df) == 25, f"expected 25 categories, got {len(df)}"

out_dir_results = "results"
df.to_csv(f"{out_dir_results}/gdp_growth_by_category.csv", index=False)

# Group-level view: simple average of the (division-derived) growth rates of
# the categories in each of the 5 existing industry_growth groups.
group_df = (
    df.groupby("industry_group")[["gdp_growth_fy21_22_pct", "gdp_growth_fy22_23_pct"]]
    .mean()
    .round(2)
    .reset_index()
    .sort_values("industry_group")
)
group_df.to_csv(f"{out_dir_results}/gdp_growth_by_group.csv", index=False)

print(df.to_string(index=False))
print()
print(group_df.to_string(index=False))

# --- Chart: line chart, FY21-22 -> FY22-23 GDP growth-rate trend per group ---
# A line/slope chart is chosen over a pie chart: the data is a rate of
# change across two time periods per category, which a pie (built for
# shares of a whole at one point in time) cannot represent; a two-point
# line makes the direction and size of the year-on-year shift legible.
COLORS = ["#4C6EF5", "#F76707", "#12B886", "#E64980", "#7048E8"]  # fixed categorical order

fig, ax = plt.subplots(figsize=(9, 6), dpi=150)
x = [0, 1]
for i, (_, row) in enumerate(group_df.iterrows()):
    y = [row["gdp_growth_fy21_22_pct"], row["gdp_growth_fy22_23_pct"]]
    ax.plot(x, y, marker="o", markersize=8, linewidth=2.5, color=COLORS[i], label=row["industry_group"])
    ax.annotate(f"{y[1]:.1f}%", (1, y[1]), textcoords="offset points", xytext=(8, 0),
                fontsize=9, color=COLORS[i], va="center")

ax.set_xticks(x)
ax.set_xticklabels(["FY2021-22", "FY2022-23"])
ax.set_ylabel("GDP (IVA) growth rate, %")
ax.set_title("GDP growth rate by merchant industry group, FY21-22 vs FY22-23\n(ABS ANZSIC-division proxy for the project's 25 merchant categories)")
ax.spines["top"].set_visible(False)
ax.spines["right"].set_visible(False)
ax.grid(axis="y", linestyle="--", alpha=0.3)
ax.legend(loc="upper left", bbox_to_anchor=(1.02, 1), frameon=False, fontsize=9)
fig.tight_layout()
fig.savefig(f"{out_dir_results}/gdp_growth_by_group_line_chart.png", bbox_inches="tight")
print("\nSaved chart to results/gdp_growth_by_group_line_chart.png")
