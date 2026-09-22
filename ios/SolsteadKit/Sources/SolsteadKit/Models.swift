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

    public let name: String
    public let version: String
    public let uptimeSeconds: Int
    public let temperatureC: Double?
    public let storage: Storage
    public let camera: Camera
    public let recordingMode: String
    public let remoteAccess: Bool
}

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
