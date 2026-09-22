# Prototype SIM shortlist

Researched 2026-09-14. These are bench candidates, not validated SIM7600G-H/firmware/carrier combinations. No purchase or activation performed. Prices below are USD, excluding tax, shipping and any account funding requirements; confirm checkout totals for Ontario.

## Recommendation

Start with one Hologram physical IoT SIM for a small, capped LTE-data and modem-originated SMS experiment. For larger data experiments, EIOTCLUB offers a cheaper prepaid physical SIM but cannot test SMS. Neither option establishes the required direct inbound VPN path. This recommendation does not change the product requirements or adopt a provider relay.

| Candidate | Advertised price | Useful scope | Limitation |
|---|---|---|---|
| Hologram self-service G3 physical SIM | $3 card, $1/month, $0.03/MB, $0.19/outbound SMS | Initial registration, outbound data and SMS experiment | Expensive video/alert volume; ordinary inbound IP blocked |
| EIOTCLUB US & Canada physical SIM | $7.99 for 100 MB/30 days; $79.99 for 6 GB/90 days | Canadian LTE data and larger transfer experiments | No SMS; no documented public inbound IP |

Hologram's rates are for new G3 activations, not historical Canada1 plans. An illustrative first month with 100 MB and 20 outgoing SMS costs $10.80 including the card, before the exclusions above. At 3,000 messages/month, SMS alone would cost $570: unsuitable at our baseline alert volume. [Current pricing](https://www.hologram.io/pricing/), [explicit USD denomination](https://www.hologram.io/es/precios/).

Hologram documents AT-command device-originated SMS, but successful SMS while this modem remains on LTE still needs bench verification. Its ordinary IP service permits outbound connections; inbound access uses its Spacebridge service, which is not our direct customer-to-controller VPN. [Communication protocols](https://docs.hologram.io/guides/sims/what-communication-protocols-are-compatible-with-hologram-sims).

Canadian G3 network operation is evidenced by Hologram's August 2026 incident notices mentioning Bell, Rogers and TELUS. Confirm current G3 LTE access to the preferred Rogers/TELUS networks in its coverage explorer before activation; this is not a promise of reception at the Huntsville site. [Provider status](https://status.hologram.io/), [coverage explorer instructions](https://docs.hologram.io/dashboard/billing/coverage-explorer).

EIOTCLUB advertises Bell/Rogers/TELUS connectivity, prepaid IoT use and a triple-cut physical SIM. Its 6 GB allowance is shared across 90 days, not renewed monthly. Select **SIM card**, not the cheaper eSIM variants on the same page. It explicitly excludes SMS and voice. [Product and plan selection](https://www.eiotclub.com/en-ca/products/north-america-sim-card).

## Compatibility and next test

### Things Mobile follow-up

User proposed Things Mobile on 2026-09-14. Its consumer shop offers a physical triple-cut SIM with €5 activation; €17 includes €12 credit. Shipping, Canadian checkout eligibility and exact modem/LTE-SMS compatibility remain unverified. [Shop](https://www.thingsmobile.com/private/shop).

The personal tariff places Canada in Zone 1+ at €0.12/MB and advertises €0.18/outgoing SMS. It adds €0.30 per accessed operator per calendar month and €1/account/month for the portal after the initial 30 days; a 5 MB monthly minimum applies. Thus 100 MB costs €12 in data alone and 3,000 SMS costs €540 at the published tariff. The generic FAQ's other-zone SMS wording differs from the tariff's all-zone wording, so confirm Canada's exact SMS charge before activation. [Personal tariff](https://www.thingsmobile.com/private/plans/pay-per-use-rate), [FAQ](https://faq.thingsmobile.com/article/623-what-are-the-features-of-the-pay-as-you-go-plans).

The advertised discount data packages explicitly cover Zone 1, whereas Canada is listed separately in Zone 1+; do not budget these packages for Canada without confirmation. [Package terms](https://www.thingsmobile.com/private/plans/individual-sim-card-data-packages).

Public static IPv4 is offered to private customers for €30 activation plus €2.50/month, but requires a predeclared source public static IP. That may enable a controlled test from a fixed-IP lab; it does not establish direct access from the owner's changing cellular iPhone IP. Using a fixed-IP customer gateway would invoke the previously reserved customer-hosted fallback. Recommendation: a possible limited bench candidate, not a better-priced default or a resolved direct-mobile-access solution. [Public-IP restrictions](https://faq.thingsmobile.com/article/757-how-can-i-request-a-public-static-ip-address).

The SIM7600G-H is an LTE Cat-4 candidate with Canadian-relevant bands, USB data and AT-command SMS support. A compatible radio band does not establish carrier acceptance or every service. Use the correct physical SIM size for the exact HAT revision, the provider's supplied APN and the manufacturer's USB networking procedure. [Waveshare setup documentation](https://www.waveshare.com/wiki/SIM7600G-H_4G_HAT_%28B%29).

First prove LTE registration and a small outbound transfer, then one SMS to the owner's iPhone while on LTE. Cap usage before enabling recurring messages or video. Record network, firmware and results in the cellular validation plan. Direct remote viewing remains a separate unresolved gate requiring a reachable endpoint; no reasonable-price individual-eligible plan satisfying all requirements has yet been verified.
