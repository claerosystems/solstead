import Foundation

/// Wire format for `GET /v1/status`.
public struct ControllerStatus: Decodable, Equatable, Sendable {
    public struct Storage: Decodable, Equatable, Sendable {
        public let ready: Bool
        public let totalBytes: Int64?
        public let freeBytes: Int64?
        public let usedBytes: Int64?
        public let error: String?
    }

    public struct Camera: Decodable, Equatable, Sendable {
        public let configured: Bool
        public let lastSuccessUnix: Int?
    }

    /// Event recording health; absent from controllers older than 0.2.
    public struct Events: Decodable, Equatable, Sendable {
        public let enabled: Bool
        public let running: Bool
        public let reason: String?
        public let bufferRunning: Bool
        public let bufferAgeSeconds: Double?
        public let motionActive: Bool
        public let cameraSubscribed: Bool
        public let cameraError: String?
        public let lastEventUnix: Int?
        public let lastError: String?
        public let count: Int
        public let usedBytes: Int64
        public let allocationBytes: Int64
    }

    public let name: String
    public let version: String
    public let uptimeSeconds: Int
    public let temperatureC: Double?
    public let storage: Storage
    public let camera: Camera
    public let events: Events?
    public let live: Live?
    public let timelapse: TimeLapseStatus?
    public let health: Health?
    public let thermal: Thermal?
    public let encryption: EncryptionStatus?
    public let recordingMode: String
    public let remoteAccess: Bool
}

extension ControllerStatus {
    /// Live view state; absent from controllers older than 0.3.
    public struct Live: Decodable, Equatable, Sendable {
        public let available: Bool
        public let active: Bool
        public let quality: String?
        public let startedUnix: Int?
        public let ready: Bool
    }
}

/// Reply to `POST /v1/live`: the stream is running and the playlist can be fetched.
public struct LiveInfo: Decodable, Equatable, Sendable {
    public let available: Bool
    public let active: Bool
    public let quality: String?
    public let ready: Bool
    public let playlist: String
}

public enum LiveQuality: String, CaseIterable, Sendable {
    case low, high
    public var label: String { self == .low ? "Low" : "Full" }
}

/// One motion-triggered event clip: pre-roll, the activity, and post-roll.
public struct Event: Decodable, Equatable, Identifiable, Sendable {
    public let id: String
    public let startedUnix: Int
    public let endedUnix: Int
    public let clipFromUnix: Int
    public let clipToUnix: Int
    public let sizeBytes: Int64
    public let trigger: String
    public let createdUnix: Int
    public let durationSeconds: Int
    /// The clip is sealed to the enrolled keys (ADR 0003); absent from controllers before it.
    public let encrypted: Bool?

    public var isEncrypted: Bool { encrypted ?? false }
    public var started: Date { Date(timeIntervalSince1970: TimeInterval(startedUnix)) }
    public var activitySeconds: Int { endedUnix - startedUnix }
}

struct EventList: Decodable { let events: [Event] }

/// One clip on the controller SSD.
public struct Recording: Decodable, Equatable, Identifiable, Sendable {
    public let id: String
    public let sizeBytes: Int64
    public let createdUnix: Int
    /// Sealed to the enrolled keys (`<id>.mp4.age`); absent from older controllers.
    public let encrypted: Bool?

    public var isEncrypted: Bool { encrypted ?? false }

    public var created: Date { Date(timeIntervalSince1970: TimeInterval(createdUnix)) }
}

struct RecordingList: Decodable { let recordings: [Recording] }
struct ErrorBody: Decodable { let error: String }

public enum ControllerJSON {
    public static let decoder: JSONDecoder = {
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        return decoder
    }()
}

/// Time-lapse state and settings, from the status block or `GET /v1/timelapse` (which adds days).
public struct TimeLapseStatus: Decodable, Equatable, Sendable {
    public let enabled: Bool
    public let intervalSeconds: Int
    public let window: String
    public let width: Int
    public let allocationBytes: Int64
    public let exportFps: Int
    public let dayCount: Int
    public let frames: Int
    public let usedBytes: Int64
    public let framesPerDay: Int
    public let estimatedRetentionDays: Int?
    public let oldestDate: String?
    public let newestUnix: Int?
    public let lastError: String?
    public let days: [TimeLapseDay]?

    /// "06:00-18:00" split into its two halves.
    public var windowStart: String { String(window.split(separator: "-").first ?? "06:00") }
    public var windowEnd: String { String(window.split(separator: "-").last ?? "18:00") }
}

public struct TimeLapseDay: Decodable, Equatable, Identifiable, Sendable {
    public let date: String
    public let frames: Int
    public let bytes: Int64
    /// Frames sealed to the enrolled keys; absent from older controllers.
    public let encryptedFrames: Int?
    public var id: String { date }

    /// The controller cannot assemble a day with any sealed frame; the phone does.
    public var isEncrypted: Bool { (encryptedFrames ?? 0) > 0 }
}

/// One stored time-lapse frame: `HHMMSS.jpg`, or `HHMMSS.jpg.age` when sealed.
public struct TimeLapseFrame: Decodable, Equatable, Sendable {
    public let day: String
    public let name: String
    public let unix: Int
    public let sizeBytes: Int64
    public let encrypted: Bool?

    public var isEncrypted: Bool { encrypted ?? name.hasSuffix(".age") }
}

struct TimeLapseFrameList: Decodable { let date: String; let frames: [TimeLapseFrame] }
struct TimeLapseHeaders: Codable { let headers: [String: Data] }
struct TimeLapseHeadersReplaced: Decodable { let replaced: Int }

/// Fields the owner may change; nil leaves a setting as it is.
public struct TimeLapseSettingsUpdate: Encodable, Equatable, Sendable {
    public var enabled: Bool?
    public var intervalSeconds: Int?
    public var window: String?

    public init(enabled: Bool? = nil, intervalSeconds: Int? = nil, window: String? = nil) {
        self.enabled = enabled
        self.intervalSeconds = intervalSeconds
        self.window = window
    }
}

public enum ControllerJSONEncoder {
    public static let encoder: JSONEncoder = {
        let encoder = JSONEncoder()
        encoder.keyEncodingStrategy = .convertToSnakeCase
        return encoder
    }()
}

extension ControllerStatus {
    /// Summary of the controller's health log.
    public struct Health: Decodable, Equatable, Sendable {
        public struct Counts: Decodable, Equatable, Sendable {
            public let info: Int
            public let warning: Int
            public let error: Int
        }

        public let clockOk: Bool
        public let lastDay: Counts
        public let latest: [HealthEntry]
    }
}

/// One line of the controller's health log.
public struct HealthEntry: Decodable, Equatable, Identifiable, Sendable {
    public let unix: Int
    public let level: String
    public let kind: String
    public let message: String

    public var id: String { "\(unix)-\(kind)-\(message)" }
    public var date: Date { Date(timeIntervalSince1970: TimeInterval(unix)) }
}

struct HealthList: Decodable { let entries: [HealthEntry] }

extension ControllerStatus {
    /// CPU temperature against the Pi's throttle thresholds, plus its power flags.
    public struct Thermal: Decodable, Equatable, Sendable {
        public let temperatureC: Double?
        public let warningC: Double
        public let throttleC: Double
        public let flagsAvailable: Bool
        public let undervoltageNow: Bool
        public let throttledNow: Bool
        public let undervoltageSinceBoot: Bool
        public let throttledSinceBoot: Bool
        /// good, warning or danger, computed by the controller.
        public let level: String
    }
}

/// `GET /v1/encryption`: which public keys new recordings are sealed to.
public struct EncryptionStatus: Decodable, Equatable, Sendable {
    public struct Key: Decodable, Equatable, Sendable, Identifiable {
        public let role: String  // "device" or "recovery"
        public let label: String
        public let publicKey: String
        public let addedUnix: Int
        public var id: String { publicKey }
    }

    public let enrolled: Bool
    public let format: String
    public let recipients: [Key]
    public let unencryptedEvents: Int?
    public let unencryptedFrames: Int?

    public var deviceKeys: [Key] { recipients.filter { $0.role == "device" } }
}

/// Body of `PUT /v1/encryption`.
public struct Enrolment: Encodable, Equatable, Sendable {
    public struct Key: Encodable, Equatable, Sendable {
        public let role: String
        public let label: String
        public let publicKey: String
    }

    public let recipients: [Key]

    /// One or more phones plus the single recovery key.
    public init(devices: [(label: String, publicKey: String)], recoveryPublicKey: String) {
        recipients = devices.map { Key(role: "device", label: $0.label, publicKey: $0.publicKey) }
            + [Key(role: "recovery", label: "Recovery key", publicKey: recoveryPublicKey)]
    }
}
