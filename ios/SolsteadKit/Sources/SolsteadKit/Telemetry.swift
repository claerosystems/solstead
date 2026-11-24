import Foundation

/// How much telemetry history a chart shows. It only changes the view; retention decides what is kept.
public enum TelemetryRange: String, CaseIterable, Identifiable, Sendable {
    case day = "24h", week = "7d", month = "30d", all

    public var id: String { rawValue }

    public var label: String {
        switch self {
        case .day: return "24 h"
        case .week: return "7 d"
        case .month: return "30 d"
        case .all: return "All"
        }
    }
}

/// Telemetry state in `GET /v1/status`; absent from controllers before telemetry.
public struct TelemetryStatus: Decodable, Equatable, Sendable {
    public let retentionDays: Int
    public let retentionChoices: [Int]
    /// False until NTP has set the controller's clock since boot; readings are held until then.
    public let clockSynchronized: Bool
    public let heldReadings: Int
    public let pendingMinutes: Int
    public let lastSampleUnix: Int?
    public let fields: [String]
}

/// Body of `PUT /v1/telemetry`.
public struct TelemetrySettingsUpdate: Encodable, Equatable, Sendable {
    public var retentionDays: Int

    public init(retentionDays: Int) {
        self.retentionDays = retentionDays
    }
}

/// `GET /v1/telemetry`: summarised points, each covering `bucketSeconds`.
public struct TelemetryHistory: Decodable, Equatable, Sendable {
    public let range: String
    public let sinceUnix: Int
    public let untilUnix: Int
    public let bucketSeconds: Int
    public let retentionDays: Int
    public let points: [TelemetryPoint]
    /// Solar harvested and system use per local day; absent from controllers without a charger reader.
    public let energy: [DailyEnergy]?

    public var since: Date { Date(timeIntervalSince1970: TimeInterval(sinceUnix)) }
    public var until: Date { Date(timeIntervalSince1970: TimeInterval(untilUnix)) }

    /// Runs of points with no missing bucket between them, so a chart can leave gaps where the
    /// controller was off rather than draw a line across them.
    public func segments(having value: (TelemetryPoint) -> Double?) -> [[TelemetryPoint]] {
        var runs: [[TelemetryPoint]] = []
        var current: [TelemetryPoint] = []
        for point in points where value(point) != nil {
            if let last = current.last, point.unix - last.unix > bucketSeconds {
                runs.append(current)
                current = []
            }
            current.append(point)
        }
        if !current.isEmpty { runs.append(current) }
        return runs
    }
}

/// One bucket of history. A measured value comes with the lowest and highest minute in the bucket;
/// a supply flag is the number of minutes it was raised.
public struct TelemetryPoint: Decodable, Equatable, Identifiable, Sendable {
    public let unix: Int
    public let minutes: Int
    public let cpuC: Double?
    public let cpuCLow: Double?
    public let cpuCHigh: Double?
    public let undervoltage: Int?
    public let throttled: Int?
    /// From the solar charger: battery volts (with the lowest single reading), charge current,
    /// solar input and the load output's use, in watts, plus the bucket's energy in watt-hours.
    public let batteryV: Double?
    public let batteryVLow: Double?
    public let batteryVHigh: Double?
    public let batteryVMin: Double?
    public let batteryA: Double?
    public let solarW: Double?
    public let solarWHigh: Double?
    public let loadW: Double?
    public let loadWHigh: Double?
    public let solarWh: Double?
    public let loadWh: Double?
    public let chargeState: String?

    public var id: Int { unix }
    public var date: Date { Date(timeIntervalSince1970: TimeInterval(unix)) }
}

/// One local day's solar harvest and system use. Minutes without a charger reading count as nothing.
public struct DailyEnergy: Decodable, Equatable, Identifiable, Sendable {
    public let date: String
    public let solarWh: Double?
    public let loadWh: Double?

    public var id: String { date }

    /// Noon of the day, so a bar sits in the middle of its day on a time axis.
    public var day: Date? {
        let parts = date.split(separator: "-").compactMap { Int($0) }
        guard parts.count == 3 else { return nil }
        return Calendar.current.date(from: DateComponents(year: parts[0], month: parts[1], day: parts[2], hour: 12))
    }
}
