# Winter test battery specification

Draft, 2026-11-01, for the first Huntsville winter test. This is a requirements specification, not a purchase recommendation: no product is selected, and capacity depends on a load measurement that has not been made.

The user found the self-heating shortlist below too expensive and set a C$400 ceiling for camera and battery together. The [budget option](#budget-option-under-c400--2026-11-01) at the end supersedes that shortlist as the current direction: it keeps the S445 and warms an ordinary battery with the controller's own heat.

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
| 4 | Built-in heater powered from the charge input, which **starts at no more than 3–4 A of available charge current**, stated in the manual; or a heater that runs from the battery's own energy and can be switched by the controller or triggered by any charge source | A 200 W panel in December often gives only a few amps. A heater that needs 6 A, like the Renogy self-heating listing in the 2026-09-18 notes, may never start on overcast days, so the battery would never charge. This is the hardest requirement to find and the main reason to reject candidates. |
| 5 | Discharge rated to -20 °C cell temperature or lower | Accepted operating target |
| 6 | Storage rated as low as possible, stated consistently in the manual (-25 °C is the best found; see the shortlist) | Huntsville nights go below -30 °C. No consumer battery checked is rated for -30 °C storage, so the enclosure has to keep the battery above its rating. |
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

## Shortlist — 2026-11-01

Checked on 2026-11-01 against manufacturer pages and Amazon.ca, browsing signed out to a Huntsville delivery address. Prices are snapshots. None of these is sold or shipped by Amazon with Prime: the Amazon.ca listings are sold and shipped by the brand. Nothing has been bought.

The deciding requirement is #4. Most self-heating batteries power their heater only from charge current, and only above a threshold of 6–20 A. A 200 W panel through the 75/15 gives about 10 A at best around noon on a clear December day, and much less when it is overcast.

### Candidates

| Battery | Capacity | How the heater gets power | Discharge / storage | Price seen | Fit |
|---|---:|---|---|---|---|
| **Volthium 12.8-200-G4DY-CH2OSS** (Quebec) | 200 Ah, 22.7 kg | Automatic heating when charge is applied below 0 °C (the activation current isn't stated for this model; Volthium's 150 Ah sheet gives 6 A, and one owner of a 300 Ah reports 15 A). There is also a separate **H+ terminal that heats from the battery's own energy** whenever voltage is applied to it. Volthium marks this for professionals and says it must be on a timer. | -25 °C / not stated | "From $1,675" on volthium.com (currency not confirmed) | **Best fit.** The controller can switch H+ through a relay using the battery probe and the Victron's solar reading, so heating does not depend on the panel reaching a threshold. Needs a fail-safe thermostat or timer in hardware, plus heater-control code. 10-year warranty; RJ45 communications rather than Bluetooth. |
| **Renogy Pro 12V 200Ah Smart, self-heating** | 200 Ah | From charge current, "a stable charge current of at least 6A"; starts below 5 °C, stops above 10 °C | -20 °C / **-25 °C** | C$1,019.99 at ca.renogy.com; Amazon.ca has no featured offer (one third-party offer at C$1,704.09) | **Off-the-shelf fallback.** Heats only on brighter middays, so on overcast stretches it will charge little or nothing once the cells are below 0 °C. Bluetooth. Has the best storage rating found. |
| **Battle Born BB10012H** | 100 Ah, 14 kg | From the battery itself, about 28 W, **whenever a charge source is present** and the cells are below about 2 °C (no current threshold) | -20 °C / not stated on datasheet | C$1,677.90 at volts.ca, listed backordered | **Meets #4 outright,** but holds only 100 Ah at the price of the 200 Ah options. Reaching 200 Ah needs two, about C$3,350, with both heaters drawing at once. Only worth it if the measured load is under about 11 W. |

### Checked and rejected (heater threshold too high)

| Battery | Heater minimum | Notes |
|---|---:|---|
| LiTime 12V 200Ah Plus self-heating | 20 A | C$655.49 on Amazon.ca; 150 W heater; storage -10 °C |
| LiTime 12V 100Ah Group 24 self-heating, two heating modes | 10 A (Regular mode) | C$429.99. "Energy-efficient" mode heats from the battery's own energy when charge is above 20%, but it can only be started from the LiTime app, so it can't run unattended unless the controller learns the Bluetooth command. Storage -10 °C. |
| Vatrer 12V 300Ah self-heating | 10 A | C$879.99 on Amazon.ca |
| Renogy Reserve Mini Smart 12V 200Ah | 15 A | C$999.99; 80 W heater |
| Renogy Core Mini 100Ah DuoHeat | 10 A per battery | C$449.99, backordered; charge rated to -30 °C |
| Power Queen 12V 100Ah self-heating | 10 A | C$419.99 on Amazon.ca |
| Redodo 12V 100Ah self-heating | 15 A | |

### DIY alternative not priced

A battery without a heater (a 12V 200Ah with low-temperature cutoff is C$454.89 on Amazon.ca) plus a 12 V silicone heating pad of about 20–40 W, switched by the controller and backed by a hardware thermostat, inside the insulated box. This is the cheapest option and the controller fully controls it, but the heater wiring, fusing and thermal safety become ours to design and verify. It is the same control scheme as the Volthium H+ terminal, without a manufacturer standing behind the heater.

### Recommendation

For a 200 Ah test, the Volthium is the one candidate whose heating does not depend on the panel. It needs a controller-switched heater relay and its fail-safe, which is new work for the controller. If that work should wait, the Renogy Pro 200Ah at about C$1,020 is the off-the-shelf choice: the winter test will then measure how often the heater cannot start. Both are well over the C$500 energy and thermal envelope in the feasibility review, so this is a test purchase, not a production direction. Before ordering the Volthium, get from Volthium the auto-heating activation current, the H+ heater current, and the storage rating for this model.

Sources: [Volthium 12V 200Ah self-heating](https://volthium.com/en/produit/12v-200ah-self-heating-battery/), [Volthium 12V 150Ah technical sheet](https://www.solacity.com/docs/Volthium/ENG-Technical-sheet-12V-150AH-2023-G31.pdf), [Victron community report on Volthium heating current](https://community.victronenergy.com/t/self-heating-battery-vs-multiplus-ii/48356), [Renogy Pro 12V 200Ah (Canada)](https://ca.renogy.com/products/pro-12v-200ah-smart-lithium-iron-phosphate-battery-w-bluetooth-self-heating), [Renogy Reserve Mini Smart 200Ah](https://ca.renogy.com/products/reserve-mini-smart-12v-200ah-314ah-lifepo4-battery-with-self-heating-bluetooth), [Renogy Core Mini DuoHeat](https://ca.renogy.com/products/core-mini-12v-100ah-lifepo4-battery-with-duoheat-tech), [Battle Born BB10012 data sheet](https://battlebornbatteries.com/cdn/shop/files/BB10012_Data_Sheet.pdf), [How Battle Born heated batteries work](https://battlebornbatteries.com/blogs/academy/how-battle-born-heated-batteries-work-the-battle-born-educational-series), [Battle Born BB10012H at Volts.ca](https://volts.ca/products/battleborn-100ah-12v-lifepo4-deep-cycle-heated-battery), [LiTime 12V 200Ah Plus (Canada)](https://ca.litime.com/products/12v-200ah-plus-self-heating-lifepo4-battery), [LiTime 12V 100Ah Group 24 self-heating (Canada)](https://ca.litime.com/products/12v-100ah-group-24-lithium-self-heating-battery), [LiTime 12V 320Ah Mini, heating modes FAQ](https://ca.litime.com/products/litime-12v-320ah-mini-smart-self-heating-lithium-battery), Amazon.ca listings B0CM9FBCBW, B0DJ957H39, B0GR91NDH5, B0CHDTYXBW, B0DLGPVF4Z, [Power Queen 100Ah self-heating](https://ipowerqueen.com/products/products-power-queen-12v-100ah-self-heating-lifepo4-battery), [Redodo 100Ah self-heating](https://www.redodopower.com/products/self-heating-redodo-12v-100ah-lithium-battery).

## Budget option under C$400 — 2026-11-01

The user set a C$400 ceiling for the camera and battery together. Two findings shape it.

**Keep the S445.** It is already bought. The VIGI models with lower published maximums cost more on Amazon.ca than the S445 did (C240 C$202.90, C340 C$209.70, C440 C$243.70), so a camera swap would use half the budget to save a few watts that have not been measured. Saving camera power is a settings job: infrared only, with the S445's white LEDs set to always off (the camera offers Auto, Scheduled, Always on and Always off), and confirmed with the 48-hour load measurement.

**Warm the battery with heat the system already makes.** The Pi, SSD, modem and converter losses turn about 5–8 W into heat in the controller enclosure. Put the battery in the same box, inside 100 mm of XPS foam board (two 50 mm layers), and that heat keeps the battery warm without a self-heating battery:

- A box about 45 × 30 × 35 cm inside has about 0.8 m² of surface. At 100 mm of XPS (about RSI 3.5), that conducts about 0.23 W/K. Allowing 50% extra for the lid seam, cable entries and corners gives about 0.35 W/K.
- 6 W of controller heat then holds the inside about 17 °C above outside. The battery stays above 0 °C, and so can charge, down to about -15 °C outside. Its 10–11 kg also smooths out overnight drops.
- Colder than that, a 12 V, 20 W silicone heating pad under the battery, switched by a W1209 thermostat (on below +3 °C, off at +8 °C), makes up the difference. Holding +3 °C at -25 °C outside takes about 9 W in total, so about 3 W average from the pad, and only in the coldest weather. Wire the pad through its own fuse on the Victron's load output so telemetry counts its energy.
- A warm battery also delivers more of its rated capacity than the 60% assumed above for a cold one. At 75% usable, a 100 Ah battery gives about 960 Wh, which covers 72 hours at up to about 13 W, including any heater energy.

This replaces requirement 4 (self-heating) with a battery that has an ordinary 0 °C charge cutoff, and changes the capacity requirement to 100 Ah if the measured load, heater included, is under about 13 W.

### Combinations

| | Battery | Heater and control | Total before foam | Note |
|---|---|---|---:|---|
| **A, in budget** | Dumfume 12V 100Ah, Amazon.ca B0DLGKGM7P, C$239.99, sold and shipped by Dumfume-CA; listed with a low-temperature cutoff | 12 V 20 W pad (B0FBGFYQBC, C$19.39) + W1209 thermostat (B099KMMJ5F, C$13.99) | **C$273.37** | Lesser-known brand. Its listing says "Low Temp Cutoff" but gives no temperature for the 100 Ah model; the brand's 200 Ah listing stops charging below 0 °C and discharges to -20 °C. Check the cutoff with the battery probe in a freezer before relying on it. |
| **B, known brand** | Renogy Core Mini 12V 100Ah, Amazon.ca B0DLBFWY41, C$351.99, shipped and sold by Renogy; blocks charging below 0 °C | Same pad and thermostat | **C$385.37** | About C$400–425 once the foam is added. |

The camera adds nothing to either (already owned). Foam: two 50 mm sheets of XPS from a hardware store, price not checked. Fuses, wire and the DS18B20 probes are already on the bench list.

Rejected at this price: ECO-WORTHY 12V 100Ah (C$299.99) and DC HOUSE 12V 100Ah (C$279.99), whose listings say charging stops only below -7 °C. That allows charging between -7 and 0 °C, which damages lithium cells. Over budget: LiTime 12V 100Ah Bluetooth with 0 °C cutoff, C$379.99 before the heater parts.

### What this costs in capability

- Three-day autonomy at 100 Ah holds only if the measured load, heater included, is under about 13 W. The 48-hour measurement decides it before buying.
- If the thermostat or pad fails, the battery still discharges normally to -20 °C. It just can't charge until it warms up. That is the same failure as a self-heating battery whose heater never gets enough current, but cheaper.
- The pad heats from the battery at night as well as by day. A later controller version could heat only when the Victron reports solar power, which saves battery energy. The thermostat stays as the hardware fail-safe.
- Insulation that holds 17 °C in winter will overheat the Pi in summer. That is a design question for after the winter test.
