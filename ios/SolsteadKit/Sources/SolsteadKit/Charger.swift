import Foundation

/// The Victron solar charger the controller reads over Bluetooth (`GET /v1/charger`; also in status).
public struct ChargerStatus: Decodable, Equatable, Sendable {
    public let configured: Bool
    public let address: String?
    public let name: String?
    /// The controller's Bluetooth scan is running.
    public let scanning: Bool
    public let error: String?
    /// Broadcasts from the chosen charger no longer decrypt with the saved key.
    public let keyMismatch: Bool
    public let lastHeardUnix: Int?
    public let reading: ChargerReading?
    /// Victron devices heard in the last ten minutes; only in `GET /v1/charger`.
    public let seen: [SeenCharger]?
}

public struct ChargerReading: Decodable, Equatable, Sendable {
    public let state: String
    public let error: Int
    public let batteryV: Double?
    public let batteryA: Double?
    public let solarW: Int?
    public let loadA: Double?
    public let loadW: Double?
    public let yieldTodayWh: Int?
}

public struct SeenCharger: Decodable, Equatable, Identifiable, Sendable {
    public let address: String
    public let name: String
    public let rssi: Int?
    public let lastHeardUnix: Int
    public let solarCharger: Bool
    public let chosen: Bool

    public var id: String { address }
}

/// Body of `PUT /v1/charger`: the charger picked from `seen` and its Instant Readout key.
public struct ChargerChoice: Encodable, Equatable, Sendable {
    public var address: String
    public var key: String

    public init(address: String, key: String) {
        self.address = address
        self.key = key
    }
}
