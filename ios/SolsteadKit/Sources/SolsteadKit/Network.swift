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

    public init(uplink: Uplink?, interface: String? = nil, wifi: WiFi? = nil, cellular: Cellular? = nil) {
        self.uplink = uplink
        self.interface = interface
        self.wifi = wifi
        self.cellular = cellular
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
