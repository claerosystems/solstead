# Initial feasibility review

Assessment date: 2026-09-13. This is a desktop engineering assessment, not prototype validation, a supplier quote, or an accepted change to requirements. No equipment has been purchased and no providers have been contacted.

## Finding

Proceed with a staged feasibility prototype, but do not yet commit to a C$500 production kit with all accepted targets. Customer-owned local recording is plausible. Three unresolved issues prevent a credible complete design: a qualifying consumer cellular connection, one-camera nighttime identification at 20 metres, and the winter energy system inside C$250 landed cost. The interview established targets; it did not demonstrate that their combination is achievable. In particular, earlier suggestions of inexpensive fixed-lens cameras should not be read as endorsements for the subsequently agreed identification requirement.

The production basis is 100 sellable units, one camera per C$500 starter kit before tax and customer delivery, C$250 maximum landed cost, and a C$1,500 initial prototype/testing budget excluding labour and certification. Two cameras may require an upgraded battery and panel. The one-camera kit still requires three-day full operation at -20°C and recharge in three winter design days while operating. Customer ownership and no company-operated services remain fixed; performance changes require explicit agreement.

| Area | Assessment | Evidence needed to close it |
|---|---|---|
| Local recording, time-lapse, encrypted export | Plausible architecture; integration untested | Camera events and streams available with its internet access blocked; recording, recovery, key handling, and phone export measurements |
| Server-independent remote viewing | Unresolved launch dependency | Consumer-eligible Canadian plan with usable inbound connectivity, SMS, modem permission, and a working iPhone path |
| 20 m, day/night, one-camera identification | Technically plausible with suitable optics, not validated at the price | Correct field of view, exposure/illumination controls, blinded face and complete-plate tests |
| Huntsville winter autonomy and recovery | Significant size/cost pressure | Measured load and cold capacity, thermal design, hourly solar simulation, then field and chamber tests |
| C$250 landed cost | No credible complete BOM established | Quotes for 100 units after optics and energy requirements are measured |

## 1. Remote access

See the separate [cellular investigation](cellular-feasibility.md) for carrier evidence and test steps. A VPN encrypts and authenticates a route; it does not create inbound reachability through a carrier firewall or NAT. A static address is not necessarily public, and a public address is not necessarily open to the required inbound traffic. Conversely, a reachable dynamic address could work if endpoint discovery is solved. Neither an eSIM nor SMS alerts resolves those questions.

No qualifying plan is established by this review. Do not infer impossibility from an unsuccessful search, but do not build the product promise around an unverified APN workaround. Optional encrypted cloud storage only supplies an outbound backup destination; it is not an approved relay for live video.

Bell documents a public static/dynamic IP service, making it the strongest initial lead; the published page does not establish consumer eligibility or all required modem/SMS terms. TELUS documents a public-IP IoT option, but its listed plans exclude SMS. Apple supports native IKEv2 configuration through its Personal VPN API, so the phone software has a credible path if the carrier route works. [Bell configuration guide](https://support.bell.ca/mobility/smartphones_and_mobile_internet/configure_mobile_devices_for_bell_mobility_network), [TELUS plans](https://docs.marketplace.telus.com/iot-console-1/rate-plans), [Apple Personal VPN](https://developer.apple.com/documentation/networkextension/personal-vpn)

## 2. Camera geometry and nighttime identification

The agreed 3 m scene width at 20 m implies approximately **8.58 degrees horizontal field of view**, calculated as `2 × atan(3 / (2 × 20))`. The 4 mm RLC-810A variant advertises 87 degrees and 3840 horizontal pixels: that covers about **38 m** at 20 m, or only **101 pixels/metre**. Cropping its image to the 3 m zone retains about 303 horizontal pixels; digital zoom does not recover absent detail. Its published -10°C lower operating limit also misses the target. [Reolink specification](https://reolink.com/us/product/rlc-810a/)

By comparison, a 1920-pixel-wide image optically framed across 3 m gives **640 pixels/metre**. An illustrative 0.30 m-wide plate spans about 192 pixels, and a 0.16 m-wide face about 102 pixels. Those illustrative dimensions and pixel counts are geometry, not a 95% identification guarantee. A 16:9 image framed 3 m wide spans only about 1.69 m vertically at that plane: lens aiming must also accommodate faces and plates at different heights and approach positions.

Axis explains that plate capture depends on pixel density, angle, exposure, focus, and illumination. Its pixel recommendations concern European plates and must not be treated as a Canadian acceptance standard. Short exposures for moving plates reduce nighttime light collection; advertised general-purpose IR range is not a plate-reading guarantee. [Axis plate-capture guidance](https://whitepapers.axis.com/en-us/license-plate-capture)

**Inference:** plate-friendly exposure may render a face too dark, while face-friendly exposure may wash out a reflective plate. The one-camera requirement therefore needs a combined test, not separate optimal settings claimed as simultaneous performance. Different compressed streams from the same exposure do not by themselves fix the optical/exposure tradeoff. Test headlights, pedestrian motion, allowed angles, and illumination transition explicitly. Motion-triggered white light cannot retroactively illuminate the pre-event buffer.

**Candidate specification, not a model selection:** a PoE camera with optical field of view reaching approximately 8.6 degrees, 2–4 MP initially, usable local detection events, controlled exposure/gain, suitable illumination, and the required operating and survival temperatures. Do not select solely by megapixels or advertised night-vision distance. The previous VIGI/Tapo shortlist demonstrated possible lower consumption, not fitness for these optics and integration requirements.

## 3. Huntsville solar and energy calculation

An initial JRC PVGIS 5.3 query used an approximate town-centre coordinate, 45.326 N / 79.216 W, free-standing crystalline silicon, south-facing 60-degree tilt, 1 kWp reference capacity, 14% system-loss input, and ERA5 years 2005–2023. Exact installation coordinates and terrain are not yet known. The returned model is saved in [the raw JSON](data/huntsville-pvgis-60deg.json). [Reproducible API query](https://re.jrc.ec.europa.eu/api/v5_3/PVcalc?lat=45.326&lon=-79.216&peakpower=1&loss=14&angle=60&aspect=0&raddatabase=PVGIS-ERA5&outputformat=json), [JRC API documentation](https://joint-research-centre.ec.europa.eu/photovoltaic-geographical-information-system-pvgis/using-pvgis-5/api-non-interactive-service_en)

| Month | Modelled mean daily plane-of-array irradiation, kWh/m²/day | Modelled PV output, kWh/day per 1 kWp |
|---|---:|---:|
| December | 2.29 | 2.00 |
| January | 3.18 | 2.86 |
| February | 4.22 | 3.76 |

These are long-term monthly means, not guarantees for three consecutive recovery days. They do not establish site tree shading, panel snow cover, battery temperature, or off-grid reliability. NRCan also cautions that monthly solar estimates do not resolve snowfall and interannual effects. [NRCan methodology](https://natural-resources.canada.ca/energy-sources/renewable-energy/photovoltaic-potential-solar-resource-maps-canada)

The calculation below uses December irradiation **2.29 equivalent full-sun hours** and an explicitly assumed **70% total irradiation-to-usable-energy factor**. It uses raw irradiation, not the PV-output column, so it does not apply a second 14% loss to the modelled PV output. The 70% factor is a sensitivity assumption covering combined PV/system conversion and storage delivery; it is not a measured efficiency. Heating is part of load, not hidden in that factor.

Let `P` be total average equivalent battery-side system demand, including camera, controller, modem, PoE/conversion losses, storage and heating. For equal average demand during discharge and recovery:

- Daily use: `24 × P Wh`.
- Three-day reserve: `72 × P Wh` of usable energy.
- Three-day replenishment while operating: deliver six days of energy in three days, or `48 × P Wh/day`.
- Panel rating: `48 × P / (2.29 × 0.70) W` before additional reliability margin.
- Illustrative nominal battery: `72 × P / 0.60 Wh`, assuming only 60% of rated energy is usable after cold capacity, ageing, discharge limits and reserve. This 60% is a scenario, not a battery specification.

| Assumed total average load | Daily use | Three-day usable reserve | Nominal battery at 60% usable | Approx. 12.8 V capacity | Panel for mean-day recovery |
|---|---:|---:|---:|---:|---:|
| 5 W | 120 Wh | 360 Wh | 600 Wh | 47 Ah | 150 W |
| 8 W | 192 Wh | 576 Wh | 960 Wh | 75 Ah | 240 W |
| 12 W | 288 Wh | 864 Wh | 1,440 Wh | 113 Ah | 360 W |
| 16 W | 384 Wh | 1,152 Wh | 1,920 Wh | 150 Ah | 480 W |

No row is a measured one- or two-camera configuration. At 8 W, changing usable battery fraction from 80% to 50% changes nominal battery need from 720 to 1,152 Wh. Changing solar delivery efficiency from 80% to 60% changes panel need from about 210 to 279 W. If recovery needs additional heating, it must be added to recovery demand explicitly.

The baseline's 12-hour “daytime” time-lapse window is not 12 hours of actual daylight during Huntsville winter. Resolve whether the intended schedule is a fixed 12-hour window or daylight-only capture. Camera/night-illumination energy must use actual winter day length, not assume 12 hours day and 12 hours night merely because the time-lapse schedule is 12 hours.

A 200 W panel supports only about **6.68 W total average load** under this illustrative recharge model. At 20% module efficiency, 240 W implies roughly 1.2 m² panel area before mounting allowances. For scale, Renogy lists its 100 W panel at 865 × 578 mm and its 195 W panel at 1262 × 764 mm; these are size examples, not selected components. [Renogy panel specifications](https://ca.renogy.com/products/renogy-16bb-n-type-195-watt-solar-panel)

Cold charging is a separate constraint from cold discharge. A Renogy 50 Ah example lists discharge down to -20°C but charging only from 0°C, with no built-in heater. The page gives conflicting storage guidance (-40°C in its table versus -25°C in its FAQ), so it cannot establish the survival requirement without clarification. [Renogy battery specifications](https://ca.renogy.com/products/core-12v-50ah-deep-cycle-lithium-iron-phosphate-battery)

Insulation and measured heat loss matter. Do not assume the battery must be held warm continuously: heating only before safe charging may save energy if the selected cells permit the required cold discharge. Conversely, a self-heated battery may require more charging current to activate than a small winter panel supplies. An additional **5 W average heating load** would add 360 Wh to three-day usable reserve and about 150 W to the illustrative recovery panel requirement. This is sensitivity arithmetic, not a heater recommendation.

A Pi Zero 2 W is a bench candidate, not a complete outdoor controller: it has 512 MB RAM, one USB OTG interface and no native Ethernet. Its published board operating minimum is -20°C, which does not certify -40°C survival. Modem, network interfaces, storage, PoE, secure key handling, and an independent temperature/power wake mechanism remain needed. Avoid assuming it can transcode 4K; initially forward camera-encoded streams. [Raspberry Pi product brief](https://datasheets.raspberrypi.com/rpizero2/raspberry-pi-zero-2-w-product-brief.pdf)

## 4. Cost and prototype scope

Observed first-party retail anchors are **C$209.99** for Renogy's 640 Wh / 50 Ah battery and **C$109.99** for the RSP100DC-CA 100 W panel variant: **C$319.98 combined**, both displayed as unavailable when checked. The panel price comes from the opened product-page SKU, not the older search snippet. These are retail references, not quotations for 100 units, and this pair is not sufficient proof of cold operation or panel sizing. [Battery listing](https://ca.renogy.com/products/core-12v-50ah-deep-cycle-lithium-iron-phosphate-battery), [Panel listing](https://ca.renogy.com/products/renogy-16bb-n-type-195-watt-solar-panel)

That pair alone exceeds the complete C$250 landed-cost target before the camera, modem, controller electronics, solar charger, PoE, thermal components, storage, enclosure, mounts, cables, assembly, testing, and inbound freight. This does not prove that an OEM design at 100 units is impossible; it does show that a simple assembly of these retail parts is not a credible production-cost plan. No supplier discounts, complete BOM, or validated retail price are claimed.

Treat C$1,500 as an initial risk-reduction budget, not a promise to fund the final prototype plus all environmental testing. A provisional allocation could reserve C$200 for cellular bench validation, C$300 for optics/illumination evaluation, C$500 for energy/thermal experimentation, C$150 for measurement accessories, and C$350 contingency. These are spending envelopes, not shopping recommendations or verified prices. They must be reallocated after quotations. Cold-chamber access, installation and recurring test connectivity remain unpriced; formal certification and labour were explicitly excluded.

## 5. Storage and data sanity checks

At the agreed 100 isolated 50-second events/day, event recording totals 5,000 seconds/day. Assuming 4 Mbit/s encoded video, this is approximately 2.5 GB/day or **17.5 GB for seven days**, before audio, encryption, filesystem and variable-bitrate overhead. Time-lapse produces 144 frames/day or **4,320 frames/camera over 30 days**; at an illustrative 0.5 MB/frame, that is 2.16 GB/camera. These are inputs to storage sizing, not a selected capacity.

One 0.2 MB snapshot per event would upload about 0.6 GB per 30 days. Ten minutes/day of 0.5 Mbit/s live preview would add about 1.125 GB/month. Full event clips at the above 4 Mbit/s rate would add about 75 GB/month. These illustrative payload totals exclude transport, retries and other traffic; camera measurements must replace them. Viewer activity can add further traffic. A data-only plan price is therefore not sufficient evidence of suitability.

## 6. Recommended validation sequence

1. **Connectivity gate:** verify consumer eligibility, SMS and modem use in provider terms; then test authenticated inbound VPN from an unrelated iPhone network, reboot/address changes, local setup without service, and the 10-second/60-second targets. Do not purchase camera hardware to solve a network requirement it cannot address.
2. **Optics gate:** use one suitable narrow-field camera to test faces and plates at 20 m, day/night, headlights, stopped vehicles and 10 km/h, while measuring power. Define test subjects, blinding, valid-pass conditions and sample size in advance. A 95% observed pass rate is not automatically statistical proof of a 95% population success rate.
3. **Energy/cost gate:** measure day/night draw, detection and illumination, cellular activity, standby, cold usable capacity, heater energy and restart. Run hourly multi-year solar/storage modelling at the actual site, with explicit snow treatment and cold shutdown rules. Obtain 100-unit quotes only against a sized design.
4. **Integrated winter gate:** only after those checks, prepare the eight-week Huntsville trial and controlled autonomy/survival tests. Record all manual interventions. Define required availability within the operating envelope so winter shutdown allowances do not conceal energy failures.

Do not commission a production PCB or full companion app before gates 1–3 establish a viable basis. The next design discussion should concern measured conflicts, not additional convenience features. If the C$500 goal conflicts with this winter target, possible changes to consider are reduced autonomy/recovery requirements, shorter identification distance, or an explicitly priced winter-power upgrade for the starter kit; none is accepted by this review. Customer ownership and no company-operated services remain unchanged.
