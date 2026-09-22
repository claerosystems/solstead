# S445 prototype camera power supply

## Scope and recommendation

User confirmed the S445 is ordered. On 2026-09-18 user chose to order the DROK 12 V / 5 A for testing; order completion not yet confirmed. Its exact-model regulation, ripple and cold performance remain unverified and require testing. MEAN WELL DDR-30G-12 remains the better-documented alternative below. This is an isolated DC/DC converter providing the required step-up/down function, not necessarily a non-isolated buck-boost topology. Do not substitute DDR-30L-12 (18 V minimum input).

[Manufacturer datasheet](https://www.meanwell.com/Upload/PDF/DDR-30/DDR-30-SPEC.PDF)
[Canadian purchase listing](https://www.digikey.ca/en/products/detail/mean-well-usa-inc/DDR-30G-12/8681205)

DigiKey indexed listing showed C$40.21 and stock on 2026-09-15; live extracted page did not expose price/stock, so these remain indicative rather than checkout-verified.

## Electrical requirements

S445 specification: 12 V ±5%, maximum 11 W, approximately 0.92 A nominal. Maintain 11.4–12.6 V at the camera including cable drop, ripple and transients. Set converter to 12.0 V before connection. Desired one-camera supply class is 24–36 W with usable current headroom at battery input voltages, rather than relying on headline power alone. Bench sweep 11–15 V, load 0–1 A plus actual startup and illuminator transitions. This sweep is not a recommended battery discharge cutoff.

DDR-30G-12: input 9–36 V, nominal output 12 V/2.5 A/30 W, adjustable output, ±2% stated voltage tolerance, 75 mV peak-to-peak maximum ripple under the manufacturer measurement method. Low-input and temperature derating apply; published typical efficiency is 86% at nominal test conditions, not measured camera-load efficiency. Operating/storage -40 to +85°C with operating derating; 35 × 90 × 54.5 mm, about 120 g, DIN-rail mount and screw terminals. Manufacturer protections include short circuit/overload/overvoltage and reverse input polarity. It is not waterproof: mount inside an appropriate dry weatherproof controller enclosure and verify enclosure temperature/condensation control.

## Wiring and validation

### PoE and USB-C bench options — 2026-09-18

User requested a 12 V input PoE injector and 12 V to 5 V / 3 A converter with USB-C plug. Amazon.ca live offers:

- PLUSPOE B0DZGCPPX8, https://www.amazon.ca/dp/B0DZGCPPX8 : C$29.92, Prime, in stock, shipped by Amazon/sold PLUSPOE. Select `POE+ 30W 802.3at, GigE`. Brand listing claims active auto-sensing af/at, 50 V / 30 W output, gigabit, terminal-block DC input. Input range conflicts between 12–48 V title and 12–36 V description; both specify a 12 V minimum, so do not assume operation on a discharged nominal-12 V battery. For bench evaluation feed from the regulated DROK 12 V branch and verify input remains within spec under load. Claimed -40 to +80°C is unvalidated. Dry enclosure needed; no verified IP rating. This is a test candidate, not production approval.
- MEIRIYFA B0BJ5WF696, https://www.amazon.ca/dp/B0BJ5WF696 : C$13.80, Prime, in stock, shipped by Amazon/sold shenzhenshi mili dianzi. Brand listing and product photo specify 8–23 V DC bare-wire input, 5 V / 3 A maximum / 15 W output and attached male USB-C lead; listing cable length 50 cm. Output accuracy, ripple, sustained 3 A performance and cold rating unverified. Suitable candidate for Pi-only bench evaluation, not a proven combined Pi/modem supply. Check Pi undervoltage under load. Do not treat potting claims as waterproof USB-C connections.

PoE path: protected battery -> DROK regulated 12 V -> PLUSPOE injector -> Ethernet to camera. Pi Ethernet -> injector LAN/data input; separate protected battery -> USB-C buck -> Pi power. Camera barrel remains disconnected with PoE. Need two Ethernet cables; no camera DC plug or PoE splitter in this path. Extra conversion losses must be measured.

Alternative direct-battery approach: Tycon TP-DCDC-1248GD (9–36 V input, 48 V / 17 W active af output per manufacturer catalog), Canadian listing https://www.digikey.ca/en/products/detail/tycon-systems-inc/TP-DCDC-1248GD/11479966 ; Prime/current price not verified. LINOVISION POE-Switch0504GD, Amazon B09HGWLZSD, also verified at C$129.99 Prime; 9–54 V input, 4 active af/at ports, 60 W total at 12 V, IP40. Switch alternative supports multiple cameras but idle consumption needs measurement. Neither alternative selected or purchased.

Battery -> appropriately rated branch fuse near positive terminal -> disconnect/low-voltage protection -> converter input; converter output pair -> camera DC connector. Pi uses its separate 5 V supply. Use short 18 AWG copper leads for bench testing. Final fuse rating/time-current curve must accommodate converter inrush and protect the selected wiring; do not infer fuse sizing from camera steady current alone. Camera connector outer diameter is specified as 5.5 mm; confirm inner diameter and polarity from delivered unit before ordering/terminating a plug.

The converter input cutoff is around 8.5 V (restart around 9 V), which does not protect a 12 V lead-acid battery from excessive discharge. Separate battery protection and unattended restart logic remain required. Initially recharge with prototype disconnected.

Validate output polarity and voltage before camera connection, then camera boot, IR/white light transitions, day/night recording, input current and converter temperature. Check voltage at the camera end of the cable. A 10 m cable requires a separate voltage-drop design; short bench leads do not validate it. Test one camera first; two-camera suitability is not assumed from the 30 W label. Prototype part is not a final production BOM or winter qualification.

## Amazon sourcing check — 2026-09-16

User prefers Amazon.ca Prime wherever practical. Exact DDR-30G-12 found at https://www.amazon.ca/dp/B07H8RDRZ8 : live selected offer C$41.23 plus C$12.56 shipping (C$53.79 before tax), in stock, shipped/sold by SKOLY, standard delivery September 23–October 1. No Prime offer verified. Recommendation remains DDR-30G-12; Amazon marketplace is an available purchasing route, with DigiKey Canada as the specialist alternative. Seller authenticity has not been independently established.

Prime alternative screened: DROK ASIN B081RG8XP5, https://www.amazon.ca/dp/B081RG8XP5 , selected 12 V / 5 A plastic-shell version. On September 15 its live offer was C$22.99, Prime, in stock, shipped by Amazon and sold by DROK. Seller advertises 9–36 V input, 12 V / 5 A output and potting. Exact-model output tolerance, ripple and cold-temperature specifications were not verified. Do not substitute it for the recommended converter for winter testing solely to obtain Prime shipping. No converter ordered.

Pololu S13V25F12 is smaller and available through RobotShop Canada, but requires soldering, has ±4% output accuracy leaving little camera/cable margin, and the reviewed page does not establish the required cold rating. Not the preferred first purchase for this user setup.
