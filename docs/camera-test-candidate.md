# Camera test candidate — 2026-09-15

## Current sub-C$200 shortlist

Prices checked in live browser on 2026-09-15; before tax/shipping. User selected the TP-Link VIGI InSight S445 with the 4 mm lens for integration/winter testing and said they will order one. User has now confirmed the S445 is ordered; arrival is not confirmed. Close-range identification testing is accepted; the 20 m requirement remains unresolved.

| Candidate | Canadian purchase | Fit and limitations |
|---|---|---|
| TP-Link VIGI InSight S445, 4 mm | [Security Camera Shop](https://www.securitycamerashop.ca/collections/tp-link-vigi/products/tp-link-vigi-insight-s445-4mm-4mp-full-color-turret-poe-camera), C$151.99, Add to cart available; delivery/stock quantity unspecified | Recommended integration and winter test candidate. 4 MP, fixed 80.4° horizontal, metal IP67, operating -30–60°C, storage -40–60°C. Manufacturer DORI identification 8 m; does not meet/prove 20 m ID. |
| Reolink RLC-811A | [Best Buy Canada](https://www.bestbuy.ca/en-ca/product/reolink-4k-ultra-hd-poe-outdoor-bullet-camera-spotlight-5x-optical-zoom-person-vehicle-alerts/15670862), C$159.99, available online, sold/shipped by ReolinkDirect | Alternative optical/detail experiment: 8 MP, 5x motorized 2.7–13.5 mm, 31° narrowest horizontal. Rated only -10–55°C; does not meet winter target. |

[S445 Canadian manufacturer specifications](https://www.vigi.com/ca/business-networking/vigi-network-camera/insight-s445/): 12 V ±5% (11 W maximum), PoE 802.3af/at (12 W maximum), H.264/H.265, RTSP, ONVIF S/T/G, web UI, human/vehicle classification, IR and white LEDs. Advanced people/vehicle analytics require specified VIGI software; do not assume those metadata are exposed over ONVIF. Local classified events and setup with cloud blocked require testing. No measured energy savings established.

[RLC-811A manufacturer specifications](https://reolink.com/gb/product/rlc-811a/?attribute_pa_version=2-pack-white): 12 V / standard PoE, under 12 W, RTSP, person/vehicle detection, IR and spotlights. At 20 m and 31° the scene is approximately 11.1 m wide: about 346 horizontal pixels/metre. Geometry is not a night face/plate identification guarantee.

The 3 m zone constraint is relaxed, but no reduced ID distance has been accepted. Neither candidate is a validated all-requirement solution. The user chose the integration/winter experiment with the S445 4 mm; this does not change the production identification requirement.

Other listings screened: C340 4 mm FleetNetwork C$94.99 sold out and manufacturer marks C340 end-of-life; C355 4 mm DirectDial C$140 backordered. Not current order recommendations.

## Superseded recommendation — rejected on price

Historical candidate: one Loryta/EmpireTech Z12E long-lens 2 MP bullet camera for an identification feasibility experiment through [Amazon.ca, ASIN B07RJQT9NB](https://www.amazon.ca/dp/B07RJQT9NB). Rejected by the user on price. New ceiling: C$200 before tax. The user permits relaxing the 3 m zone width and is reconsidering mandatory PoE; reduced identification distance is not accepted. No replacement selected. Technical details below preserve the earlier research, not a current purchase recommendation.

Live browser check: C$460.48, in stock, sold and shipped by Amazon US through Amazon.ca, import charges included, free delivery estimated September 22, 30-day return/refund eligibility displayed. Final tax and return conditions belong to checkout. This is an Amazon.ca purchase but an imported item, not Canadian distributor stock.

Listing ambiguity: title IPC-HFW5241E-Z12E, item model IPC-B52IR-Z12E, detailed description IPC-B52IR-Z12E-S2. Older/newer specifications coexist, including different IR-distance claims. Verify the delivered label, firmware and specifications before outdoor installation. Do not promise a particular revision or flash firmware for an assumed Dahua equivalent.

## Technical basis

The brand's S2 specification lists: 1920×1080; 5–60 mm motorized lens; 59°–5° horizontal view; manual shutter and IR control; human/vehicle classification; RTSP, ONVIF S/G/T and CGI; 12 V or 802.3af PoE; IP67/IK10; operating −30 to +60°C, storage −40 to +60°C. Published PoE consumption is 4.6 W basic and 12.1 W maximum, not measured average. [Brand specifications](https://empiretech01.com/products/empiretech-2mp-wdr-ir-bullet-ai-network-bullet-camera-ipc-b52ir-z12e-s2).

Our geometry requires 2×atan(3/40) = 8.58° horizontally for a 3 m-wide zone at 20 m, within the stated zoom range. At that framing, 1920/3 = 640 pixels/metre: an illustrative 30 cm plate spans 192 pixels and a 16 cm face 102 pixels. This is sampling geometry, not an identification guarantee. Vertical coverage is about 1.69 m at that plane: aiming must include faces and plates, or use slightly wider framing and remeasure detail.

The key experiment is readable plates AND identifiable faces with one operational night configuration. Reflective plates can overexpose while faces remain dark. Separate optimal settings do not satisfy the combined requirement. Face detection is not identification of a named person; automatic plate-to-text recognition is not assumed.

Local classified-event delivery and operation with camera Internet access blocked require testing. Initially record/forward camera-encoded H.264 without transcoding; generate time lapse on the Pi from local frames.

## Cost and bench equipment

The camera alone nearly consumes the proposed C$500 complete retail price. It is a prototype instrument, not evidence of a viable C$250 landed production BOM. Its energy demand may also require a different production camera or explicitly agreed tradeoff. Product requirements remain unchanged.

Use the existing Pi 4, Ethernet cables and a standards-based PoE injector/switch. The Pi Ethernet port cannot power the camera. [TP-Link TL-POE160S at Canada Computers](https://www.canadacomputers.com/en/power-injector/188906/tp-link-tl-poe160s-poe-injector-tl-poe160s.html) is a candidate: 802.3af/at, 30 W, integrated AC supply; price/fulfillment not verified. No branded NVR is planned. Select weather-protected cable junction hardware after checking the delivered camera.

Proposed bench topology: Pi Ethernet → injector DATA IN; injector DATA+POWER OUT → camera; Pi Wi-Fi for administration. Configure a separate camera subnet. Do not expose camera ports directly to cellular Internet.

## Acceptance sequence

1. Check identity and settings; verify local H.264 streaming and classified events with camera Internet blocked.
2. Mark 20 m distance and 3 m width; test day/night focus and reboot recovery.
3. Capture approaching pedestrians and stopped/≤10 km/h vehicles, daylight/darkness/headlights, including simultaneous face/plate scenes with consenting participants.
4. Sweep exposure/IR settings, retain originals and score complete plates and blinded face matches separately. Run a small pilot before the agreed 95% acceptance trials.
5. Measure day/night/event/restart power and test pre-event recording, time lapse and controller recovery.
6. Test LTE after local operation works; resolve optics before buying the winter power system.

## Canadian alternatives screened

- Noctoptics: Hikvision iDS-2CD7A46G2/LM-IZHSY 8–32 mm, C$978.88, sold out. [Listing](https://noctoptics.com/products/ids-2cd7a46g2-lm-izhsy-8-32mm).
- Security Camera Shop: Hikvision iDS-2CD7A46G0/P-IZHSY8, C$1,199.99, sold out. [Listing](https://www.securitycamerashop.ca/products/hikvision-ds-3e1526p-ei-24-port-gigabit-smart-poe-switch-copy).
- IT Devices: Vivotek IB9367-EHT-V2 5–50 mm, C$1,165.84, sourced on demand; delivery unconfirmed. [Listing](https://www.itdevices.ca/product-details/788874).

No non-Canadian checkout is proposed. Amazon variant ambiguity remains explicitly unresolved.
