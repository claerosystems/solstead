# Cellular feasibility — Canada launch

Research date: 2026-09-13. Documentary assessment only; no provider contacted and no SIM or hardware tested.

## Finding

**Direct remote viewing is technically plausible, but no Canadian cellular plan has been verified to satisfy all launch requirements together:** individual-customer eligibility, allowed unattended modem use, direct inbound reachability, usable video data allowance, and controller-to-phone SMS. Treat connectivity as a launch gate, not an implemented solution. This is a bounded search result, not proof that a qualifying plan does not exist.

A controller-hosted VPN needs a routable address and carrier permission for inbound traffic. A static address can be private; a public address can be dynamic. IPv6 support alone does not establish inbound firewall behavior or reachability from an IPv4-only phone network. An outbound VPN only helps if there is a reachable peer/server; it does not independently solve carrier NAT. Encrypted off-site storage is useful for uploaded evidence but does not establish the live-view path.

## Documented leads

| Lead | Primary-source evidence | Assessment |
|---|---|---|
| Bell Mobility public static/dynamic IP | Bell's current [mobile configuration guide](https://support.bell.ca/mobility/smartphones_and_mobile_internet/configure_mobile_devices_for_bell_mobility_network) explicitly mentions subscribers to Public–Static/Dynamic IP service and differing APNs. | Best direct-carrier lead to investigate. Page does not establish individual eligibility, price, inbound VPN ports, modem terms, or SMS on the same plan. No qualifying plan verified. |
| TELUS IoT public IP | [APN documentation](https://docs.marketplace.telus.com/iot-console-1/access-points-names-apns) specifies public static APN `psw.telus.iot`, assigned by support request. [Rate plans](https://docs.marketplace.telus.com/iot-console-1/rate-plans) list C$12/SIM/month public-IP add-on, C$16.49/month for 1 GB or C$33.49 for 5 GB; voice and SMS are not included. | Documented public-IP capability; not a verified launch match. Business Marketplace positioning does not establish individual eligibility. SMS exclusion requires resolution, not an assumption that modem SMS works. Listed prices need reconfirmation. |
| Rogers standard consumer APN | [Current settings](https://www.rogers.com/support/mobility/apn-settings) list IPv4/IPv6 for `ltemobile.apn` and tethering APN `ltedata.apn`. | Does not promise inbound access. Historical public-IP APN discussions were not used as present-day commercial eligibility evidence. |
| Hologram | [Protocol documentation](https://docs.hologram.io/guides/sims/what-communication-protocols-are-compatible-with-hologram-sims) lists ordinary Direct IP as outbound only, with inbound through Spacebridge. It documents device-originated SMS and purchased-number inbound SMS. [2025 release notes](https://docs.hologram.io/release-notes/release-notes-2025) mention assigned public IPs. | Public-IP exceptions may exist, but purchasable terms, Canada eligibility and price are unverified. Ordinary Spacebridge path introduces the deferred provider tunnel dependency. Do not infer a consumer-ready public-IP plan from release notes. |
| 1NCE | [VPN documentation](https://help.1nce.com/docs/network-services/network-services-vpn-service/) describes private SIM addresses behind NAT with access via 1NCE OpenVPN endpoints. [SMS documentation](https://help.1nce.com/dev-hub/docs/portal-sims-sms) says SIMs cannot send/receive SMS to external parties. | Not a direct substitute for the accepted direct VPN plus phone-SMS architecture. Provider VPN is an additional service dependency, and included IoT SMS is not phone alert SMS. |

## iPhone path

Apple supports IKEv2/IPsec with certificate authentication. Its [Personal VPN API](https://developer.apple.com/documentation/networkextension/personal-vpn) lets an app configure and connect the built-in VPN after initial user authorization. Therefore a controller-hosted IKEv2 service with customer/device-generated credentials is a credible prototype: no company VPN service or embedded shared company credential is inherently required. Start with a manually installed test profile before app integration. [Apple VPN security](https://support.apple.com/en-ca/guide/security/sec802e8ab55/web)

Proposed implementation constraints: pair locally, bind the controller's cryptographic identity, give each authorized person separate credentials, expose only the VPN listener, and route only system traffic through the tunnel. These are design proposals. Test coexistence with a customer's existing VPN, reconnect behavior, cold boot, certificate renewal without company infrastructure, revocation, and first-video latency. A native protocol also permits a documented manual fallback if the companion app later becomes unavailable.

## Endpoint discovery and SMS

Prefer a public static address for the first test. A dynamic public address needs an update path after cellular reconnection. SMS can carry the new address as text; that is a proposed manual import/copy workflow, not verified seamless iPhone automation. Do not assume the companion app can silently read the SMS inbox. Apple's documented [message-filter API](https://developer.apple.com/videos/play/wwdc2017/249/) serves unknown-sender filtering, not a general device-control inbox.

Authenticate any imported endpoint update against the locally paired device identity; never send private keys or bearer access tokens in SMS. A forged or stale text must not authorize a new device. SMS metadata and plain alert text remain visible to the messaging infrastructure. A dynamic-DNS provider would be another dependency requiring a product decision. Using customer storage as an endpoint directory would also create a required storage dependency for live viewing and cannot silently replace the agreed optional-backup model.

## Next validation gate

1. Obtain written terms for a candidate offered to individuals: unattended modem permitted, public IPv4 or globally reachable IPv6, required inbound VPN traffic permitted, SMS to Canadian phones supported, and all recurring/overage charges.
2. Test exact SIM/APN/modem in Huntsville. Test from independent phone carriers and external Wi-Fi, not merely from the same LAN. Confirm routing and filtering after reattach, reboot, idle time and address changes.
3. Measure the agreed targets: 95% of SMS within 60 seconds; 95% of live sessions showing a current frame within 10 seconds including VPN startup. Measure modem power in weak signal as well as good signal.
4. With 100 events/day, one SMS/event is about 3,000 outbound messages/month per recipient before status messages. Validate allowance, automated-message terms and anti-abuse behavior; invited recipients multiply this traffic. Snapshot and video bytes require a separate measured budget.

If no compatible consumer plan can be validated, launch remote viewing remains unresolved. The accepted options would need revisiting explicitly: allow a customer-chosen relay earlier, allow required customer infrastructure, or change account eligibility. None is silently adopted here.
