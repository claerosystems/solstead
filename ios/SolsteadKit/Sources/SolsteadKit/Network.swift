import Foundation

/// How the controller reaches the internet, and how strong its radios are.
public struct NetworkStatus: Decodable, Equatable, Sendable {
    public enum Uplink: String, Decodable, Sendable {
        case wifi, cellular, ethernet, other
    }

    public struct WiFi: Decodable, Equatable, Sendable {
        public let interface: String
        public let signalPercent: Int
        public let signalDbm: Int?
    }

    /// Which link the owner wants the controller to use; Wi-Fi until they choose. The controller
    /// still falls back to the other link when the preferred one has no internet.
    public enum Preference: String, Codable, CaseIterable, Sendable {
        case wifi, cellular

        public var name: String {
            switch self {
            case .wifi: return "Wi-Fi"
            case .cellular: return "Cellular"
            }
        }
    }

    public struct Cellular: Decodable, Equatable, Sendable {
        public let signalPercent: Int?
        public let technology: String?
        public let `operator`: String?
        public let state: String?
    }

    /// nil when the controller has no default route.
    public let uplink: Uplink?
    public let interface: String?
    public let wifi: WiFi?
    public let cellular: Cellular?
    /// nil from controllers that can't switch links, or that sent a value this app doesn't know.
    public let preferred: Preference?
    /// True while the controller is on the other link because the preferred one failed its checks.
    public let fallback: Bool
    /// Why the controller fell back, in words for the owner ("Wi-Fi has no internet").
    public let fallbackReason: String?

    public init(uplink: Uplink?, interface: String? = nil, wifi: WiFi? = nil, cellular: Cellular? = nil,
                preferred: Preference? = nil, fallback: Bool = false, fallbackReason: String? = nil) {
        self.uplink = uplink
        self.interface = interface
        self.wifi = wifi
        self.cellular = cellular
        self.preferred = preferred
        self.fallback = fallback
        self.fallbackReason = fallbackReason
    }

    private enum CodingKeys: String, CodingKey {
        case uplink, interface, wifi, cellular, preferred, fallback, fallbackReason
    }

    public init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        // An uplink kind this app doesn't know is shown as "other" rather than failing the whole status.
        let uplinkName = try container.decodeIfPresent(String.self, forKey: .uplink)
        uplink = uplinkName.map { Uplink(rawValue: $0) ?? .other }
        interface = try container.decodeIfPresent(String.self, forKey: .interface)
        wifi = try container.decodeIfPresent(WiFi.self, forKey: .wifi)
        cellular = try container.decodeIfPresent(Cellular.self, forKey: .cellular)
        preferred = (try? container.decodeIfPresent(String.self, forKey: .preferred)).flatMap(Preference.init(rawValue:))
        fallback = (try? container.decodeIfPresent(Bool.self, forKey: .fallback)) ?? false
        fallbackReason = try? container.decodeIfPresent(String.self, forKey: .fallbackReason)
    }

    /// Whether the controller lets the owner choose a link.
    public var supportsPreference: Bool { preferred != nil }

    /// "Using Cellular (LTE): Wi-Fi has no internet." while fallen back; nil otherwise.
    public var fallbackNote: String? {
        guard fallback, uplink != nil else { return nil }
        let wanted = preferred.map { "\($0.name) isn't working" } ?? "the preferred link isn't working"
        let reason = fallbackReason.flatMap { $0.isEmpty ? nil : $0 } ?? wanted
        return "Using \(uplinkName): \(reason.hasSuffix(".") ? String(reason.dropLast()) : reason)."
    }

    public var uplinkName: String {
        switch uplink {
        case .wifi: return "Wi-Fi"
        case .cellular: return "Cellular" + (cellular?.technology.map { " (\($0.uppercased()))" } ?? "")
        case .ethernet: return "Ethernet"
        case .other: return interface ?? "Other"
        case nil: return "No internet"
        }
    }

    /// Signal of the radio in use, 0-100; nil for wired links or no reading.
    public var uplinkSignalPercent: Int? {
        switch uplink {
        case .wifi: return wifi?.signalPercent
        case .cellular: return cellular?.signalPercent
        default: return nil
        }
    }

    /// An SF Symbol for the uplink; draw `wifi` and `cellularbars` with `variableValue`.
    public var symbolName: String {
        switch uplink {
        case .wifi: return "wifi"
        case .cellular: return "cellularbars"
        case .ethernet: return "cable.connector"
        case .other: return "network"
        case nil: return "wifi.slash"
        }
    }

    /// One word for a 0-100 signal reading.
    public static func strength(_ percent: Int) -> String {
        switch percent {
        case 70...: return "Strong"
        case 50..<70: return "Good"
        case 30..<50: return "Fair"
        default: return "Weak"
        }
    }

    /// "Strong, 83% (-52 dBm)" style text for a reading.
    public static func describe(percent: Int?, dbm: Int? = nil) -> String {
        guard let percent else { return "No reading" }
        return "\(strength(percent)), \(percent)%" + (dbm.map { " (\($0) dBm)" } ?? "")
    }
}

/// Body of `PUT /v1/network`.
public struct NetworkPreferenceUpdate: Encodable, Equatable, Sendable {
    public var preferred: NetworkStatus.Preference

    public init(preferred: NetworkStatus.Preference) {
        self.preferred = preferred
    }
}
