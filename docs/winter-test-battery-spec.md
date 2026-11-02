# Winter test battery specification

Draft, 2026-11-01, for the first Huntsville winter test. This is a requirements specification, not a purchase recommendation: no product is selected, and capacity depends on a load measurement that has not been made.

## What the battery has to do

- Run the one-camera system (VIGI S445, Raspberry Pi 4 controller with SSD, SIM7600G-H modem, DROK 12 V and 5 V converters) for three days at -20 °C with no solar input, the accepted autonomy target in the [feasibility review](feasibility-review.md).
- Accept charge from the Victron SmartSolar MPPT 75/15 chosen for telemetry (about 220 W of panel at 12 V) with whatever a 100–200 W panel delivers in December.
- Recover by itself after cold nights or a deep discharge. Nobody should have to visit the site to press a button or attach a mains charger.
- Show its state in telemetry: voltage and charge state through the Victron, cell temperature through the battery DS18B20 probe.

## Load assumption

The battery-side load has not been measured. Estimate, to be replaced by measurement:

| Load | Estimated average | Basis |
|---|---:|---|
| S445 camera | 5–9 W | 11 W published maximum; infrared on through about 15 hours of December night |
| Pi 4 with SSD | 3–5 W | Recording, HLS on demand, time-lapse |
| SIM7600G-H on LTE | 1–2 W | WireGuard keepalives keep the radio from idling fully |
| Converter losses | 10–15% | Two converters; a PoE injector would add its own loss |
| **Total** | **10–18 W, about 13 W midpoint** | |

## Capacity

Using the feasibility review's rule (`72 × P / 0.60` Wh nominal, where 60% covers cold capacity, BMS window and reserve):

| Measured average load | Nominal energy needed | At 12.8 V |
|---:|---:|---:|
| 8 W | 960 Wh | 75 Ah |
| 10.7 W | 1,280 Wh | 100 Ah |
| 13 W | 1,560 Wh | 122 Ah |
| 18 W | 2,160 Wh | 169 Ah |
| 21 W | 2,560 Wh | 200 Ah |

At the estimated load, **200 Ah** is the size that meets three days with margin. 100 Ah meets it only if the measured load is under about 11 W. Measure before buying: 48 hours on the Victron's load output gives a day-and-night average, including infrared.

## Requirements

| # | Requirement | Why |
|---|---|---|
| 1 | LiFePO4, 12.8 V nominal (4S), 10–14.6 V operating range | Matches the 12 V converters and the Victron. The PLUSPOE injector needs at least 12 V, so a PoE path must stay behind the regulated DROK output. |
| 2 | Capacity from the table above: 200 Ah unless the measured load is under 11 W | Three days at -20 °C |
| 3 | Charging blocked below 0 °C **cell** temperature by the BMS, with discharge still allowed | Charging lithium below freezing plates the cells; the system must keep running while charging is blocked |
| 4 | Built-in heater powered from the charge input, which **starts at no more than 3–4 A of available charge current**, stated in the manual | A 200 W panel in December often gives only a few amps. A heater that needs 6 A, like the Renogy self-heating listing in the 2026-09-18 notes, may never start on overcast days, so the battery would never charge. This is the hardest requirement to find and the main reason to reject candidates. |
| 5 | Discharge rated to -20 °C cell temperature or lower | Accepted operating target |
| 6 | Storage rated to -30 °C or lower, stated consistently in the manual | Huntsville nights go below -30 °C; the earlier Renogy page gave conflicting storage figures |
| 7 | After a low-voltage or low-temperature cutoff, the BMS resumes by itself when charge voltage appears, with no button or mains "wake" | No site visits during the test |
| 8 | Continuous discharge at least 20 A | Load is 1–2 A; any 100 Ah or larger unit exceeds this |
| 9 | Optional: Bluetooth BMS showing charge state, cell voltages and cell temperature | A second view to check Victron telemetry against. Not a telemetry source yet. |
| 10 | M8 terminals, a single battery (not two in parallel) | Two self-heating batteries in parallel each need heater current from the same small panel |
| 11 | Amazon.ca with Prime preferred, as for other prototype parts | Purchasing preference, 2026-09-15 |

## Settings and installation that go with it

- **Victron charging:** LiFePO4 profile (absorption 14.2 V, float 13.5 V, or the battery manual's values), temperature compensation off, and the Victron's own low-temperature cut-off **off**. The battery's BMS handles cold. If the charger also stops when cold, the heater never gets the current it needs.
- **Victron load output:** set its low-voltage disconnect above the BMS cutoff (for example off at 12.0 V and back on at 13.0 V; check against the battery's curve) so the BMS never disconnects the battery and the charger always sees a battery to charge. The reduced-power mode thresholds go above this.
- **Placement:** put the battery in the insulated controller enclosure so the Pi's, modem's and converters' waste heat (about 4–6 W) warms it. Fix the battery DS18B20 probe against the battery case, away from the heat sources.
- **Protection:** fuse or DC breaker at the battery positive, sized to the wiring and converter inrush, as in the [camera power specification](camera-power-spec.md).

## What the battery cannot solve

Recovery is a panel problem. At 13 W, the three-day recovery target needs about 390 W of panel (`48 × P / (2.29 × 0.70)`), almost twice what the 75/15 can take. A larger battery lengthens what the test can observe through bad weather, but it does not close that gap. The heater's energy also comes out of the harvest. The first winter test should measure harvest, load, battery temperature and heater behaviour, and not be read as passing or failing the recovery target with this panel.
