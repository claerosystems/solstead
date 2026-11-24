# Winter test solar panel specification

Draft, 2026-11-01, for the first Huntsville winter test, alongside the [winter test battery specification](winter-test-battery-spec.md). No panel had been specified before this; earlier notes discussed only "100–200 W". Not purchased.

## Constraints

- Smallest practical panel. Product notes allow a rigid panel of roughly 1 m × 1 m provisionally, but smaller is preferred.
- Unshaded, south-facing, on an adjustable winter mount, up to 5 m of cable from the controller.
- The Victron SmartSolar MPPT 75/15 takes at most about 220 W at 12 V and 75 V open-circuit.

## What a panel of about 1 m² can do

PVGIS 5.3 at 45.326 N / 79.216 W, using the 70% irradiation-to-usable-energy factor from the [feasibility review](feasibility-review.md). These are long-term monthly means, with no snow cover and no shading:

| Month | Irradiation on a 60° panel, kWh/m²/day | Usable from 200 W | Load that breaks even |
|---|---:|---:|---:|
| November | 2.86 | 400 Wh/day | 16.7 W |
| December | 2.29 | 320 Wh/day | 13.4 W |
| January | 3.18 | 445 Wh/day | 18.5 W |
| February | 4.22 | 590 Wh/day | 24.6 W |

At the estimated 13 W load (pad included), 200 W only breaks even on an average December day. It runs a deficit through dull weeks, which the 100 Ah battery covers, and builds a surplus from January. Meeting the accepted target of refilling in three winter days while operating would need about 390 W in December, roughly 2 m and nearly twice what the 75/15 accepts. With a panel limited to about 1 m², that target holds in December only if the load is under about 6.7 W. The 48-hour load measurement shows how far short the test system falls; the winter test then shows how the battery copes with real weather.

A 100 W panel (about 0.5 m²) gives about 160 Wh/day in December, which supports only about 6.7 W. That is too small unless the measured load is that low.

## Tilt

PVGIS output per kWp for a south-facing panel:

| Tilt | November | December | January | February |
|---|---:|---:|---:|---:|
| 60° | 2.42 | 2.00 | 2.86 | 3.76 |
| 75° | 2.39 | 2.00 | 2.85 | 3.64 |
| 90° | 2.21 | 1.89 | 2.67 | 3.31 |

Mount at **75°**. It yields the same as 60° in December and January, and sheds snow better, which PVGIS does not model. Vertical costs about 5% in December and 12% in February.

## Panel requirements

| # | Requirement | Why |
|---|---|---|
| 1 | About 200 W total, at most 220 W | Breaks even near the expected load; the 75/15's limit |
| 2 | About 1 m² or less in total, rigid glass and aluminium frame | Size allowance; a mount that holds a steep winter tilt |
| 3 | 12 V nominal panels (Voc about 22 V each), two wired in series if two panels | In series, the 75/15 starts charging earlier in weak morning light. Two 22 V panels give about 44 V, about 51 V at -30 °C, under the 75 V limit. Check the label Voc. |
| 4 | Snow load rating stated (5,400 Pa is typical) | Wet snow on a steep panel |
| 5 | MC4 connectors, cable sized for 5 m of run at 15 A or less (10 AWG) | Product cable-distance target |
| 6 | Bifacial preferred if the price is the same | At a steep tilt over snow, the back face picks up reflected light. The gain is not quantified here. |

## Candidates (Amazon.ca, 2026-11-01)

| Panel | Size | Price | Note |
|---|---|---:|---|
| **ECO-WORTHY 2 × 100 W N-type bifacial** ([B0B3RW7HC9](https://www.amazon.ca/dp/B0B3RW7HC9)) | Each 905 × 585 mm; side by side about 1.17 × 0.91 m (1.06 m²) | **C$189.99** | Recommended. Cheapest, bifacial, 5,400 Pa snow load. Sold by ECO-WORTHY CA and shipped by Amazon. Two panels in series. |
| Renogy 195 W N-type ([B0F43P3SNW](https://www.amazon.ca/dp/B0F43P3SNW)) | 1262 × 764 mm (0.96 m²), 10.6 kg | C$239.99 | One panel, so a simpler mount and no series connection, but 1.26 m long. Sold and shipped by Amazon.ca; 6 left. |
| 2 × Renogy 100 W N-type ([B0CTLMH7FC](https://www.amazon.ca/dp/B0CTLMH7FC)) | Each 865 × 578 mm; side by side about 1.16 × 0.87 m | C$239.98 | Known brand, smallest footprint of the 200 W options. Sold and shipped by Amazon.ca. |

Not chosen: unbranded single 200 W panels (C$159.99–179.99, 1.34 m long), flexible panels (they don't hold a steep tilt and wear quickly), and 100 W alone (too small, see above).

Still needed and not priced: a 75° frame (aluminium angle or a tilt-mount kit), 10 AWG MC4 extension cable, and a DC breaker or fuse between panel and charger.
