import Foundation

/// The one seam between the client and the network, so tests can stub the controller.
public protocol ControllerTransport: Sendable {
    func send(_ request: URLRequest) async throws -> (Data, URLResponse)
}

extension URLSession: ControllerTransport {
    public func send(_ request: URLRequest) async throws -> (Data, URLResponse) {
        try await data(for: request)
    }
}

public enum ControllerError: LocalizedError, Equatable {
    /// The controller answered with an error it wants the owner to read (401, 503, ...).
    case controller(String, status: Int)
    case unexpectedResponse(status: Int)
    case invalidImage

    public var errorDescription: String? {
        switch self {
        case let .controller(message, _): return message
        case let .unexpectedResponse(status): return "Unexpected controller response (\(status))."
        case .invalidImage: return "The controller returned an invalid camera image."
        }
    }
}

/// Typed access to the controller's v1 API.
public struct ControllerClient: Sendable {
    public let pairing: Pairing
    private let transport: ControllerTransport

    public init(pairing: Pairing, transport: ControllerTransport) {
        self.pairing = pairing
        self.transport = transport
    }

    /// A client whose session trusts only the pinned certificate and keeps nothing on disk.
    public static func pinned(to pairing: Pairing, requestTimeout: TimeInterval = 45) -> ControllerClient {
        let configuration = URLSessionConfiguration.ephemeral
        configuration.timeoutIntervalForRequest = requestTimeout
        configuration.timeoutIntervalForResource = requestTimeout + 5
        configuration.waitsForConnectivity = false
        let session = URLSession(configuration: configuration, delegate: PinnedTrustDelegate(pairing: pairing), delegateQueue: nil)
        return ControllerClient(pairing: pairing, transport: session)
    }

    public func status() async throws -> ControllerStatus {
        try decode(ControllerStatus.self, from: await request("v1/status"))
    }

    public func recordings() async throws -> [Recording] {
        try decode(RecordingList.self, from: await request("v1/recordings")).recordings
    }

    /// A JPEG frame. `width` nil uses the controller's preview size; 0 asks for the camera's
    /// full resolution.
    public func snapshot(width: Int? = nil) async throws -> Data {
        try await request(width.map { "v1/camera/snapshot?width=\($0)" } ?? "v1/camera/snapshot")
    }

    /// Starts live view (or switches quality). The controller answers once the first
    /// segments exist, which can take several seconds.
    public func startLive(quality: LiveQuality = .low) async throws -> LiveInfo {
        try decode(LiveInfo.self, from: await request("v1/live?quality=\(quality.rawValue)", method: "POST"))
    }

    public func stopLive() async throws {
        _ = try await request("v1/live", method: "DELETE")
    }

    /// Fetches a live playlist or segment by file name through the pinned session.
    public func liveResource(_ name: String) async throws -> Data {
        try await request("v1/live/" + name)
    }

    public func timelapse() async throws -> TimeLapseStatus {
        try decode(TimeLapseStatus.self, from: await request("v1/timelapse"))
    }

    public func updateTimelapse(_ update: TimeLapseSettingsUpdate) async throws -> TimeLapseStatus {
        let body = try ControllerJSONEncoder.encoder.encode(update)
        return try decode(TimeLapseStatus.self, from: await request("v1/timelapse", method: "PUT", body: body))
    }

    /// Downloads a day's time-lapse video (assembled by the controller) to a temporary file.
    public func downloadTimelapseVideo(day: String) async throws -> URL {
        let data = try await request("v1/timelapse/\(day)/video.mp4")
        return try writeTemporary(data, name: "timelapse-\(day).mp4")
    }

    /// The controller's health log, newest first. `minimumLevel` is info, warning or error.
    public func health(minimumLevel: String = "info", limit: Int = 100) async throws -> [HealthEntry] {
        try decode(HealthList.self, from: await request("v1/health?level=\(minimumLevel)&limit=\(limit)")).entries
    }

    public func events() async throws -> [Event] {
        try decode(EventList.self, from: await request("v1/events")).events
    }

    /// Downloads an event clip to a temporary file and returns its URL. AVPlayer cannot use the
    /// pinned session directly, so clips are fetched through it and played from disk.
    public func downloadEventClip(_ event: Event) async throws -> URL {
        try writeTemporary(try await request("v1/events/\(event.id)/clip"), name: event.id + ".mp4")
    }

    private func writeTemporary(_ data: Data, name: String) throws -> URL {
        let folder = FileManager.default.temporaryDirectory.appendingPathComponent("solstead-clips", isDirectory: true)
        try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
        let url = folder.appendingPathComponent(name)
        try data.write(to: url, options: .atomic)
        return url
    }

    @discardableResult
    public func record() async throws -> Recording {
        try decode(Recording.self, from: await request("v1/recordings", method: "POST"))
    }

    // MARK: - Internals

    func makeRequest(_ path: String, method: String, body: Data? = nil) -> URLRequest {
        let parts = path.split(separator: "?", maxSplits: 1).map(String.init)
        var url = pairing.url.appendingPathComponent(parts[0])
        if parts.count == 2, var components = URLComponents(url: url, resolvingAgainstBaseURL: false) {
            components.percentEncodedQuery = parts[1]
            url = components.url ?? url
        }
        var request = URLRequest(url: url)
        request.httpMethod = method
        request.setValue("Bearer " + pairing.token, forHTTPHeaderField: "Authorization")
        request.setValue("application/json, image/jpeg", forHTTPHeaderField: "Accept")
        if let body {
            request.httpBody = body
            request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        }
        return request
    }

    private func request(_ path: String, method: String = "GET", body: Data? = nil) async throws -> Data {
        let (data, response) = try await transport.send(makeRequest(path, method: method, body: body))
        guard let http = response as? HTTPURLResponse else { throw ControllerError.unexpectedResponse(status: 0) }
        guard (200..<300).contains(http.statusCode) else {
            if let body = try? ControllerJSON.decoder.decode(ErrorBody.self, from: data) {
                throw ControllerError.controller(body.error, status: http.statusCode)
            }
            throw ControllerError.unexpectedResponse(status: http.statusCode)
        }
        return data
    }

    private func decode<T: Decodable>(_ type: T.Type, from data: Data) throws -> T {
        try ControllerJSON.decoder.decode(type, from: data)
    }
}
