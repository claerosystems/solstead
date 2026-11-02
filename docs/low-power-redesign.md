# Low-power redesign under C$500

Proposal, 2026-11-01, pending the user's acceptance. It supersedes the battery and panel choices in the [winter test battery spec](winter-test-battery-spec.md) and [winter test solar panel spec](winter-test-solar-panel-spec.md) if accepted.

The user asked for a total price under C$500 for everything, a smaller system, and lower power, and allowed a camera change and a shorter range. This reads "everything" as every part of the winter test system, priced at Canadian retail. It is not the C$250 landed production cost, which retail parts can't reach.

## Why the current system can't fit

The bench system (S445, Pi 4 with SSD, SIM7600 Cat-4 modem, two converters) is estimated at 10–18 W. At 13 W it needs a 100 Ah battery and a 200 W panel of about 1 m², and even then only breaks even in December. Battery, panel, charger and enclosure alone come to more than C$500 before the camera and controller. Reducing that load is the only route to a small, cheap system: every watt saved removes about 24 Wh of battery per day of autonomy and about 15 W of December panel.

## The design: one box, camera built in

Replace the separate network camera and the Pi 4 with a **Raspberry Pi Zero 2 W and a Raspberry Pi Camera Module 3 NoIR in one small enclosure**, looking out through a window. The camera then needs no Ethernet, PoE or 12 V converter, and no camera computer runs alongside the controller. The Zero 2 W encodes H.264 in hardware, so the controller still records the camera's encoded video as it does now. The panel stays separate on its cable.

### Power budget (estimates, to be measured on the bench)

| Load | Estimate | Measures that cut it |
|---|---:|---|
| Pi Zero 2 W capturing and encoding 1080p continuously for the pre-event buffer, with motion detection on a low-resolution second stream | 1.3–1.8 W | No SSD; Wi-Fi and HDMI off; 1080p at 15 fps instead of 25 |
| Camera Module 3 | included above | |
| LTE modem, connected, tunnel kept open | 0.5–1.0 W | Longer WireGuard keepalive, once the carrier's NAT timeout is measured |
| 850 nm infrared, on only after dark | 0.6 W averaged over a December day | A low-power board left on at night so the pre-event buffer isn't black, boosted only during events |
| 12 V to 5 V conversion loss | 0.3 W | One converter instead of two |
| **Total** | **2.7–3.7 W, design figure 3.5 W** | About a quarter of the current system |

### What 3.5 W needs

Same rules as the [feasibility review](feasibility-review.md): 60% usable capacity when cold, December irradiation of 2.29 kWh/m²/day with a 70% delivery factor.

- **Battery:** three days needs 252 Wh usable, so 12.8 V **30 Ah** (384 Wh). Two days needs **20 Ah**.
- **Panel:** **100 W** (905 × 585 mm, about half the 200 W panel's area) refills three days of use in three December days while running: `48 × 3.5 / (2.29 × 0.70)` ≈ 105 W. It breaks even at 52 W, so ordinary December days end with a surplus. This meets the accepted recovery target, which the 13 W system could not.
- **Cold charging:** the controller's 3.5 W of waste heat won't keep a small battery above 0 °C. Instead the controller **switches a 12 W heating pad only when the panel is producing**, so the sun warms the battery before it charges, and **opens a relay in the charge line below +2 °C**. That relay is normally open, so charging stops if the controller dies, whatever the battery's own BMS does. The cheap 30 Ah batteries checked either stop charging only below -7 °C (DC HOUSE) or don't state a cutoff (ECO-WORTHY, generic), so the controller has to enforce it. A heater that runs only on solar costs no battery energy.

## Bill of materials

Prices seen on 2026-11-01 (PiShop.ca, Amazon.ca); "est." means not checked.

| Part | Choice | Price |
|---|---|---:|
| Controller | Raspberry Pi Zero 2 W with header (PiShop.ca) | C$25.95 |
| Camera | Raspberry Pi Camera Module 3 NoIR, 76° (PiShop.ca) | C$36.45 |
| Infrared | 12 V 850 nm LED boards, 2-pack (Amazon.ca B0GJ3823TG) | C$18.34 |
| Modem | Waveshare SIM7670G LTE Cat-1 HAT, global bands (Amazon.ca B0CXP7ZPVL) | C$75.32 |
| Storage | 128 GB high-endurance microSD | C$25 est. |
| 12 V to 5 V | MEIRIYFA 5 V / 3 A buck (already on the bench list) | C$13.80 |
| Battery | 12.8 V 30 Ah LiFePO4, e.g. ECO-WORTHY B08VD88Q9K | C$132.99 |
| Panel | ECO-WORTHY 100 W N-type bifacial (Amazon.ca B00V4844F4) | C$112.99 |
| Charge controller | 10 A MPPT for LiFePO4, e.g. B0FMY5HSZ2 | C$35.51 |
| Battery heating and charge cutoff | 12 V 12 W silicone pad (B0CLS2BMSV) C$15.89, relay or MOSFET module about C$10, DS18B20 probe already listed | C$26 est. |
| Power telemetry | INA226 current/voltage sensor in place of the Victron | C$10 est. |
| Clock | DS3231 RTC module | C$6 est. |
| Enclosure | Small weatherproof box, 50 mm XPS lining, clear camera window | C$40 est. |
| Wiring | Fuses, 10 AWG MC4 extension, mount hardware | C$40 est. |
| **Total** | | **about C$599** |

That is over C$500 by about C$100. Ways to close it, in order of preference:

1. **Use the owned SIM7600G-H for the winter test** (−C$75): about C$524. It draws more than a Cat-1 modem, but the test measures that; the production choice can wait.
2. **20 Ah battery and two-day autonomy** (−C$43, e.g. B0FGTYWL26 at C$89.99): about C$481 with step 1.
3. Enclosure and wiring are estimates; reusing bench parts may bring them down.

**Under C$500 needs steps 1 and 2 together.** With three-day autonomy kept, the test build is about C$524.

The Victron 75/15 drops out of this design: a 100 W panel fits a 10 A controller, and the INA226 measures system use directly. If a Victron is already bought, it can stand in for the MPPT and INA226 (about C$45).

## Requirement changes this needs (pending the user's acceptance)

| Current requirement | Proposed | Why |
|---|---|---|
| Identify people and read plates at 20 m in a 3 m zone | **About 5 m**: at 1080p over the Camera Module 3's 66° horizontal view, the 250 pixels per metre usually taken for identification reaches about 5.9 m. A narrower M12 lens on the HQ Camera M12 (C$72.45) would roughly double that, for more cost and size. | The camera's field of view and 1080p encoder |
| Separate camera on a cable, controller elsewhere | **Camera built into the controller box.** A second camera means a second unit. | Removes the network camera, PoE, the converter and Ethernet |
| Recordings on a separate drive, never on the OS card | **One microSD card, with recordings on their own partition** | The Zero 2 W's only USB port carries the modem |
| Main-stream recording at 2688 × 1520, 25 fps | **1080p at 15 fps** | The Zero 2 W's encoder limit, and lower power |
| Three-day autonomy at -20 °C | **Two days**, only if needed to reach C$500 (step 2) | Battery cost and size |

Kept: the 10-second pre-event buffer, remote live view within 10 seconds, encryption, time-lapse, and recovery in three winter days.

## Risks

- **The power figures are estimates.** Measure first: a Zero 2 W and camera cost C$62. Bench-measure them recording continuously, and the modem connected over the tunnel, before buying the battery or panel.
- **The Zero 2 W is rated to -20 °C.** It sits in the insulated box with its own heat, but this hasn't been tested.
- **The camera window** can frost, collect condensation, and reflect the infrared back into the lens. Mount the infrared outside or shield it from the window.
- **Software work:** the controller takes its video from the Pi camera instead of the S445's network stream and ONVIF motion, and has to detect motion itself. It also gains a charge relay, a solar-only heater, and the INA226 power source in telemetry.
- **512 MB of RAM** must hold the controller, the in-memory live view segments and encryption. It hasn't been tested.

## Not chosen

- **Keep the Pi 4 and fit a smaller network camera:** about 7–8 W, so a 60 Ah battery and a 200 W panel, well over C$500.
- **Sodium-ion battery** (charges down to -20 °C, so no heater needed): 12 V 30 Ah packs are about £129 in the UK ([Bimble Solar NA30](https://www.bimblesolar.com/NA30)), with no Canadian retail source found. Worth revisiting for production.
- **Self-heating 30 Ah battery** (LiTime, C$208.99, needs at least 5 A to heat): more than the controller-switched pad, and a 100 W panel only reaches 5 A near midday.
