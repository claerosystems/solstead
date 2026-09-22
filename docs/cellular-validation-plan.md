# Cellular validation plan

2026-09-15. Status: the user reports ordering the equipment following the Pi 4 recommendation. Treat the Pi 4 and SIM7600G-H as the intended ordered bench configuration; exact delivered contents and accessories remain to be checked. The user has ordered a physical Public Mobile SIM for testing, replacing the proposed Bell SIM. Activation and exact plan terms are not yet confirmed. No carrier contact, network configuration or live test has been performed by the assistant.

## Available equipment and gap

Plan update, 2026-09-15: the user selected Public Mobile “50GB Data at 5G Speed” and can change it. Record 50 GB as the stated allowance; price, billing period, exact SMS terms and activation status are not confirmed. Recommend keeping this selection for initial limited bench tests rather than changing solely because the SIM7600G-H is LTE Cat-4. It cannot use 5G radio service. This recommendation does not establish modem eligibility, LTE SMS or inbound reachability. Measure data use before selecting a smaller ongoing allowance.

Latest carrier decision, 2026-09-15: Public Mobile physical SIM ordered for the controller; this supersedes the Bell Connected Things proposal and earlier Rogers/TELUS preferences for this prototype. The owner’s iPhone also uses Public Mobile. Exact controller plan, modem eligibility, LTE SMS and inbound VPN reachability remain unverified. Earlier Bell/provider material below is historical research, not the current selected service. Remote-access testing must include an unrelated network as well as the owner’s phone.

Accepted bench recommendation; equipment order reported 2026-09-15: Raspberry Pi 4 Model B, 2 GB RAM, official 5.1 V / 3 A USB-C supply and 32 GB microSD for the bench. It has a fitted 40-pin header, USB-A ports, Ethernet and Wi-Fi. KORE explicitly tested the standard SIM7600G-H HAT with a Pi 4 Model B; this provides a documented hardware integration baseline, not Bell service validation. Use the short USB data connection as documented even when the HAT is stacked. The Pi 4 is a development host recommendation, not the selected production solar controller. [Documented integration](https://docs.korewireless.com/supersim/supersim-first-steps/get-started-with-super-sim-the-raspberry-pi-4-and-the-waveshare-4g-hat), [Pi specifications](https://www.raspberrypi.com/products/raspberry-pi-4-model-b/).

HAT listing supplied: [Amazon Canada ASIN B0824P4B7M](https://www.amazon.ca/dp/B0824P4B7M). The listing appears to correspond to the standard SIM7600G-H 4G HAT; direct Amazon retrieval failed, so exact shipped board revision and bundle contents remain to be checked. Do not use the previously linked (B) board's pogo-pin instructions by assumption. KORE's first-party integration guide for the standard board documents the modem port labelled USB, not USB TO UART. The Pi 4 supersedes the earlier Pi Zero adapter/hub proposal; no OTG adapter or extra hub is planned for basic Pi 4 bring-up. Final power topology depends on the received board. Keep the physical Bell SIM's size adapters until the holder is checked. [Standard-board integration reference](https://docs.korewireless.com/supersim/supersim-first-steps/get-started-with-super-sim-the-raspberry-pi-4-and-the-waveshare-4g-hat).

Additional host confirmed: Raspberry Pi Zero v1.2. Retained as spare hardware; the Pi 4 is now the intended bench host. It is an original Zero, not a Zero 2 W; integrated camera/VPN performance is unvalidated. Its single USB OTG data connection complicates simultaneous modem and local administration/Ethernet. Confirm the exact HAT product link before selecting cables, power topology or assuming pogo-pin compatibility. [Pi Zero specifications](https://www.raspberrypi.com/products/raspberry-pi-zero/).

Host update: the user owns a Pi marked “Raspberry Pi (c)2011.12”. This suggests an early board but does not establish the exact model/revision. Obtain the model and revision from the running OS before selecting software or wiring. An early Pi may suffice for basic USB modem/AT-command/data tests; do not assume its GPIO layout or USB power can support the HAT directly. This is not yet a validated integrated camera/VPN host.

2026-09-14 current selection: the user is reserving the ordered Blues Notecard and Raspberry Pi HAT for another project. For this prototype, the user intends to order a Waveshare SIM7600G-H 4G HAT and a regular physical Bell SIM for a Connected Things data plan. The user explicitly confirmed a regular physical SIM, replacing the proposed Bell eSIM plus eSIM.me adapter. No eSIM.me card or Android provisioning device is needed for this configuration. Equipment order subsequently reported on 2026-09-15; Bell SIM acquisition/activation remains unconfirmed. The exact Waveshare board revision and Bell plan remain unverified. Bell supersedes the earlier Rogers/TELUS preference for this bench test.

Bell must confirm the selected plan is available on a physical SIM and permits use in this modem. Public inbound access and LTE SMS remain separate unverified requirements; the user's selection is not evidence that either service is included. [Bell activation guidance](https://support.bell.ca/mobility/activating-bell-connected-things-online?step=2).

The user has an iPhone with a data plan and a SIM800-based GSM/GPRS Raspberry Pi board, and can obtain a dedicated test SIM. The Pi 4 is now the intended bench host; older Pi boards are also available. Use the iPhone as the remote endpoint. Do not order a SIM specifically for the SIM800 to validate this product.

SIM800 is a GSM/GPRS (2G) family, not LTE. SIMCom lists the SIM800C family member at up to 85.6 kbit/s; the exact existing board variant remains unknown. That is below even the illustrative 500 kbit/s live preview used in the feasibility calculation. [SIMCom specifications](https://www.simcom.com/product/SIM800C.html)

Rogers currently schedules 2G retirement for November 30, 2027. Remaining 2G availability is therefore not a sustainable product basis, nor proof of coverage or activation eligibility at the site. A 2G SMS demonstration would not establish SMS behavior on the future LTE modem. [Rogers retirement notice](https://www.rogers.com/support/mobility/2g-network-retirement-everything-you-need-to-know)

## Candidate bench arrangement

Dedicated LTE SIM → LTE USB modem → Linux host running authenticated VPN and a timestamped test video stream → iPhone over a separate network. Begin on mains power; measure energy after connectivity works. A real camera is unnecessary for the first network test, but later integrated startup tests must use the actual camera.

Waveshare's SIM7600G-H 4G HAT (B) was the researched candidate; the user has reported an equipment order for the SIM7600G-H bench configuration, with exact revision still to confirm: its manufacturer documents LTE Cat-4, USB networking and AT-command SMS. Those features suit a Linux bench setup, but global-band marketing does not establish Canadian carrier approval, provisioned SMS over LTE, or direct inbound routing. Select the exact modem SKU and firmware only after carrier confirmation. Host USB mode and any additional modem NAT layer must be understood. [Waveshare product](https://www.waveshare.com/product/raspberry-pi/boards-kits/raspberry-pi-zero-2-w-cat/sim7600g-h-4g-hat-b.htm), [manufacturer setup documentation](https://www.waveshare.com/wiki/SIM7600G-H_4G_HAT_%28B%29)

Bell warns that network retirement can affect data, text and voice depending on device compatibility. Verify SMS while registered on LTE with legacy fallback unavailable; successful LTE data alone is insufficient. [Bell retirement guidance](https://support.bell.ca/mobility/network_coverage/hspa_network)

## Provider confirmation before selecting a plan

See the [prototype SIM shortlist](prototype-sim-shortlist.md) for purchasable bench candidates researched on 2026-09-14. Hologram can support a limited data/SMS experiment; EIOTCLUB is data-only. Neither is a validated solution for the complete direct-VPN requirement.

Current carrier selection: the remote iPhone remains on Public Mobile; the user now intends to use Bell Connected Things for the controller. Earlier Rogers/TELUS research is retained as alternatives. Bell plan eligibility, SMS and inbound routing are not established by this selection.

Rogers' standard [APN page](https://www.rogers.com/support/mobility/apn-settings) does not promise inbound VPN reachability. TELUS documents a [public static IoT APN](https://docs.marketplace.telus.com/iot-console-1/access-points-names-apns), but its [IoT rate plans](https://docs.marketplace.telus.com/iot-console-1/rate-plans) exclude SMS; these business-oriented documents do not validate an individual-customer offer. Ask both providers for a specific supported plan instead of using historical APN workarounds. Lack of documented consumer eligibility is an unresolved question, not proof of unavailability.

Bell's configuration page acknowledges Public–Static/Dynamic IP service but directs customers to support for details. Its consumer Mobile Internet page lists a Freedom plan starting at C$30/month for up to 30 GB, with usage-based tiers. This does **not** establish that the advertised plan includes public IP, modem-originated SMS, or permission for automated alerts. Do not substitute phone-plan unlimited-text claims for confirmation on the actual modem plan. [Bell APN guidance](https://support.bell.ca/mobility/smartphones_and_mobile_internet/configure_mobile_devices_for_bell_mobility_network), [Mobile Internet plans](https://www.bell.ca/Mobility/Cell_phone_plans/Turbo-Stick-and-Turbo-Hub-data-plans)

The following inquiry is prepared for the user to send or use during a call; it has not been sent:

> I am an individual customer in Ontario evaluating a remotely accessed security camera controller near Huntsville. I need a separately billed SIM for an unattended LTE modem, not a phone hotspot or a business-only account. My remote iPhone uses Public Mobile. Please confirm a currently orderable Rogers/TELUS plan and add-on meeting all of these requirements together:
>
> - Individual/personal account eligibility and permitted unattended camera/modem use.
> - Publicly reachable IPv4, preferably static, or an IPv6 alternative with documented inbound reachability. For IKEv2, inbound UDP 500 and 4500 must be permitted; please identify any carrier firewall or NAT restrictions and required APN.
> - Outgoing SMS directly from that modem to ordinary Canadian mobile numbers while on LTE, without relying on 2G/3G. Approximately 3,000 SMS/month per recipient, plus occasional status messages, with customer-requested automated security notifications permitted.
> - At least roughly 2–5 GB/month of usable video/snapshot data for initial testing, with all overage, automatic tiering, public-IP, activation, SIM and cancellation charges disclosed.
> - Supported modem models and firmware, including whether SIM7600G-H devices are permitted and provisioned for both LTE data and SMS. If not, please identify a supported alternative.
>
> Please provide the exact plan/add-on names and written terms. A private static address, outbound-only VPN support, or SMS accessible only through a provider portal would not meet this test requirement.

Do not infer a binding permission decision from generic website text. If public IP is business-only or alerts require a different service, record the incompatibility and revisit requirements with the user rather than silently choosing a workaround.

## Tests once the equipment and plan are available

1. Record modem model/firmware, SIM plan/APN, registration technology, signal metrics and interface addresses. Keep SIM identifiers and credentials out of shared reports. Confirm local host connectivity and SMS capability independently.
2. Compare modem/host addressing and routing. An apparent public address or successful ping is not proof that the VPN listener is reachable. Use a real authenticated tunnel from the iPhone with Wi-Fi disabled; also test from an unrelated Wi-Fi network. Avoid inferring success from two devices on the same LAN.
3. Run a customer-credentialed IKEv2 listener on the host or a supported router. If the modem/router performs NAT, configure only the needed VPN forwarding. Keep camera streaming/admin ports behind the VPN. Native IKEv2 configuration is documented by [Apple](https://developer.apple.com/documentation/networkextension/personal-vpn); exact host configuration follows hardware selection.
4. Initially prove one SMS and one tunnel/video session. Then test idle reconnect, airplane-mode cycles, modem reboot, host power loss, IP-address changes and certificate identity checks. A public dynamic IP additionally requires a working endpoint-update workflow; no company DNS/relay service is assumed.
5. For a pilot measurement, run 100 startup trials and 100 individually identified SMS events across multiple times/conditions. Record time from View to first current frame (target ≤10 s) and detection trigger to actual phone SMS receipt (target ≤60 s). Count non-delivery and startup failures; do not replace phone receipt time with modem submission time. At least 95/100 in each test is an initial observed target, not statistical proof of long-term 95% reliability.
6. Exercise good and weak LTE signal, independent phone networks, and reconnect after outage. LTE lock/testing method must follow the selected modem documentation. Recheck at the actual Huntsville site. Synthetic-stream success is only a network gate, not final camera acceptance.
7. Measure total payload/overhead and modem power, then revise plan and energy calculations. Repeating 100 events/day to multiple viewers multiplies SMS traffic. Ask before sending test messages to anyone other than explicitly authorized test recipients.

## Evidence table

| Requirement | Current status |
|---|---|
| Remote iPhone available | User confirmed; Public Mobile; model unknown |
| Suitable LTE modem available | No; existing SIM800 is 2G |
| Individual-eligible public-IP + LTE SMS plan | Not verified |
| Carrier permission for automated SMS/modem | Not verified |
| Direct VPN and endpoint recovery | Not tested |
| SMS ≤60 s / live view ≤10 s targets | Not tested |
| Huntsville signal and energy | Not tested |

Next dependency: obtain a provider-confirmed plan/modem combination. The documentation supports an inquiry and bench design, not an assertion that cellular validation has passed.

## Arrival sequence

1. Check the received Pi, HAT revision, antenna, USB data cable, power supply, microSD and mounting parts against the setup documentation.
2. Boot and configure the Pi on the home network before adding the modem; obtain software over home Internet.
3. With power disconnected, install the SIM and antenna and assemble the modem using the confirmed board instructions.
4. Verify USB detection and modem identity, then LTE registration and one small data transfer.
5. Test modem-originated SMS to the owner's iPhone if enabled and permitted on the selected Public Mobile plan.
6. Test authenticated direct VPN reachability separately; successful outbound data does not pass this gate.

Next interview dependency: confirm the intended Public Mobile plan name, price and allowance, and whether it has been activated. Do not collect SIM identifiers, activation codes or account credentials in the design notes.
