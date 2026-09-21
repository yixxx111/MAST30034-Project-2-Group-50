# GDP growth rates for the 25 merchant categories (extra task)

Not one of the four assigned pipeline modules — a separate, additional task:
find FY2021-22 and FY2022-23 GDP growth rates for the project's 25 merchant
categories and chart them.

## Data source

Real ABS data, not estimated: **"Australian Industry"** release, Industry
Value Added (IVA) by ANZSIC division, current prices ($m):
- [Australian Industry, 2021-22](https://www.abs.gov.au/statistics/industry/industry-overview/australian-industry/2021-22) — gives FY2020-21 → FY2021-22 growth
- [Australian Industry, 2022-23](https://www.abs.gov.au/statistics/industry/industry-overview/australian-industry/2022-23) — gives FY2021-22 → FY2022-23 growth

These releases report growth at the **ANZSIC division** level (18-19 broad
divisions such as "Retail trade", "Construction", "Information media and
telecommunications"). ABS does not publish GDP/GVA figures at the level of
this project's 25 informal merchant categories (e.g. "florists supplies,
nursery stock, and flowers", "shoe shops") — no such official series exists.

## Method (a judgement call, stated explicitly)

Each of the 25 merchant categories is mapped by hand to the single ANZSIC
division whose real-world activity is the closest match, and that
division's official YoY IVA growth rate is used as a proxy for the
category. This is an approximation, not a category-specific measurement.
The mapping is in `build_gdp_growth.py` (`category_map`) and the output CSV.

**Limitation to be upfront about:** most of the 25 categories are specialty
retail shops, so most of them map to the single "Retail trade" division and
therefore carry identical growth figures (12.4% in FY21-22, 14.4% in
FY22-23) in `gdp_growth_by_category.csv`. Only categories that are clearly
not retail — telecom/digital media, computer programming services,
equipment rental, beauty/repair services — map to a different division. The
25-category table is included because that's what was asked for, but the
**group-level view is the more informative one**: it averages the (already
division-derived) growth rates within each of the 5 existing
`industry_growth` groups, giving 5 distinct trajectories instead of a table
that is mostly one repeated number.

## Files

- `build_gdp_growth.py` — builds both CSVs and the chart
- `results/gdp_growth_by_category.csv` — all 25 categories, their matched
  ANZSIC division, and the FY21-22 / FY22-23 growth rate
- `results/gdp_growth_by_group.csv` — the 5 industry_growth groups, mean
  growth rate for FY21-22 and FY22-23
- `results/gdp_growth_by_group_line_chart.png` — line chart, FY21-22 vs
  FY22-23, one line per group

## Chart type: line, not pie

The user's brief allowed either. A pie chart shows shares of a whole at one
point in time; growth *rates* across two periods aren't shares, so a pie
would misrepresent the data (and can't show FY21-22 vs FY22-23 together
without two separate pies, which is harder to compare than one line chart).
A two-point line/slope chart makes the direction and size of the
year-on-year change directly readable, so that's what was built.

## Result

| Industry group | FY21-22 | FY22-23 |
|---|---|---|
| Art, Gifts, Jewellery & Fashion | 12.40% | 14.40% |
| Creative, Books & Leisure | 12.40% | 14.40% |
| Digital, Technology & Communications | 13.68% | 9.10% |
| Home, Garden & Living | 13.00% | 12.20% |
| Mobility, Health & Specialist Services | 14.28% | 15.68% |

Digital, Technology & Communications is the only group whose growth slowed
markedly between the two years (driven by the telecom/media division's
growth falling from 13.9% to 6.2%); the other four groups held roughly
steady or accelerated slightly.
