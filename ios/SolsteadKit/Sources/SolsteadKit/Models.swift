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

    public var started: Date { Date(timeIntervalSince1970: TimeInterval(startedUnix)) }
    public var activitySeconds: Int { endedUnix - startedUnix }
}

struct EventList: Decodable { let events: [Event] }

/// One clip on the controller SSD.
public struct Recording: Decodable, Equatable, Identifiable, Sendable {
    public let id: String
    public let sizeBytes: Int64
    public let createdUnix: Int

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
