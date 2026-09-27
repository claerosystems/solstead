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

    /// The most a listing returns; asking for this many means "all of them".
    public static let listingLimit = 100_000

    /// Manual clips, newest first; the controller returns 200 unless `limit` says otherwise.
    public func recordings(limit: Int? = nil) async throws -> [Recording] {
        try decode(RecordingList.self, from: await request("v1/recordings" + (limit.map { "?limit=\($0)" } ?? ""))).recordings
    }

    /// Just the age header of a sealed manual clip, for re-wrapping.
    public func recordingHeader(_ recording: Recording) async throws -> Data {
        try await request("v1/recordings/\(recording.id)/header")
    }

    /// Replaces a sealed manual clip's header with one that adds a recipient; returns the clip.
    @discardableResult
    public func replaceRecordingHeader(_ recording: Recording, with header: Data) async throws -> Recording {
        try decode(Recording.self, from: await request("v1/recordings/\(recording.id)/header", method: "PUT", body: header,
                                                         contentType: "application/octet-stream"))
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
    /// A day with sealed frames answers 409; see `assembleTimelapse` for those.
    public func downloadTimelapseVideo(day: String) async throws -> URL {
        let data = try await request("v1/timelapse/\(day)/video.mp4")
        return try writeTemporary(data, name: "timelapse-\(day).mp4")
    }

    /// A day's frames in time order, sealed ones marked.
    public func timelapseFrames(day: String) async throws -> [TimeLapseFrame] {
        try decode(TimeLapseFrameList.self, from: await request("v1/timelapse/\(day)")).frames
    }

    /// One frame as stored: a JPEG, or an age file when sealed.
    public func timelapseFrame(_ frame: TimeLapseFrame) async throws -> Data {
        try await request("v1/timelapse/\(frame.day)/\(frame.name)")
    }

    /// The age header of every sealed frame of a day, by frame name, for re-wrapping.
    public func timelapseHeaders(day: String) async throws -> [String: Data] {
        try JSONDecoder().decode(TimeLapseHeaders.self, from: await request("v1/timelapse/\(day)/headers")).headers
    }

    /// Replaces sealed frames' headers with re-wrapped ones; returns how many were replaced.
    @discardableResult
    public func replaceTimelapseHeaders(day: String, _ headers: [String: Data]) async throws -> Int {
        let body = try JSONEncoder().encode(TimeLapseHeaders(headers: headers))
        return try decode(TimeLapseHeadersReplaced.self,
                          from: await request("v1/timelapse/\(day)/headers", method: "PUT", body: body)).replaced
    }

    /// What restoring access did to a set of sealed recordings (a day's frames, or all event clips).
    public struct RewrapCount: Equatable, Sendable {
        public var unlocked = 0, already = 0, unreadable = 0
        public init() {}
    }

    /// Adds `device` to every sealed frame of a day that `recovery` opens, re-wrapping only the
    /// headers and uploading them in batches. Frames sealed under another key are counted, not changed.
    public func rewrapTimelapseDay(_ day: String, using recovery: AgeIdentity, adding device: AgeIdentity,
                                   batch: Int = 500) async throws -> RewrapCount {
        var count = RewrapCount()
        var pending: [String: Data] = [:]
        for (name, header) in try await timelapseHeaders(day: day).sorted(by: { $0.key < $1.key }) {
            if Age.canOpen(header: header, with: [device]) {
                count.already += 1
                continue
            }
            do {
                pending[name] = try Age.rewrap(header: header, using: recovery, adding: device.recipient)
            } catch SealingError.noMatchingKey {
                count.unreadable += 1  // sealed under an earlier setup with a different recovery key
                continue
            }
            if pending.count >= batch {
                count.unlocked += try await replaceTimelapseHeaders(day: day, pending)
                pending = [:]
            }
        }
        if !pending.isEmpty { count.unlocked += try await replaceTimelapseHeaders(day: day, pending) }
        return count
    }

    /// The controller's health log, newest first. `minimumLevel` is info, warning or error.
    public func health(minimumLevel: String = "info", limit: Int = 100) async throws -> [HealthEntry] {
        try decode(HealthList.self, from: await request("v1/health?level=\(minimumLevel)&limit=\(limit)")).entries
    }

    public func events(limit: Int? = nil) async throws -> [Event] {
        try decode(EventList.self, from: await request("v1/events" + (limit.map { "?limit=\($0)" } ?? ""))).events
    }

    /// Deletes an event's clip and record from the controller.
    public func deleteEvent(_ event: Event) async throws {
        _ = try await request("v1/events/\(event.id)", method: "DELETE")
    }

    /// Where downloaded clips are kept while they play. Anything left here is a leftover.
    public static var clipFolder: URL {
        FileManager.default.temporaryDirectory.appendingPathComponent("solstead-clips", isDirectory: true)
    }

    /// Just the age header of a sealed clip (a few hundred bytes), for re-wrapping.
    public func eventHeader(_ event: Event) async throws -> Data {
        try await request("v1/events/\(event.id)/header")
    }

    /// Replaces a sealed clip's header with one that adds a recipient; returns the updated event.
    @discardableResult
    public func replaceEventHeader(_ event: Event, with header: Data) async throws -> Event {
        try decode(Event.self, from: await request("v1/events/\(event.id)/header", method: "PUT", body: header,
                                                     contentType: "application/octet-stream"))
    }

    /// Downloads an event clip to a temporary file and returns its URL. AVPlayer cannot use the
    /// pinned session directly, so clips are fetched through it and played from disk.
    /// Sealed clips are decrypted on the phone with `identities` and only the plaintext copy is
    /// returned; the sealed download is deleted.
    public func downloadEventClip(_ event: Event, identities: [AgeIdentity] = []) async throws -> URL {
        let data = try await request("v1/events/\(event.id)/clip")
        guard event.isEncrypted || Age.isSealed(data.prefix(32)) else {
            return try writeTemporary(data, name: event.id + ".mp4")
        }
        guard !identities.isEmpty else { throw SealingError.noMatchingKey }
        let sealed = try writeTemporary(data, name: event.id + ".mp4.age")
        defer { try? FileManager.default.removeItem(at: sealed) }
        let plain = sealed.deletingPathExtension()
        try Age.open(fileAt: sealed, to: plain, with: identities)
        return plain
    }

    public func encryption() async throws -> EncryptionStatus {
        try decode(EncryptionStatus.self, from: await request("v1/encryption"))
    }

    /// Replaces the keys new recordings are sealed to. Recordings already sealed are unchanged.
    public func enroll(_ enrolment: Enrolment) async throws -> EncryptionStatus {
        let body = try ControllerJSONEncoder.encoder.encode(enrolment)
        return try decode(EncryptionStatus.self, from: await request("v1/encryption", method: "PUT", body: body))
    }

    /// Turns encryption off: new recordings are stored unencrypted; sealed ones stay sealed.
    public func disableEncryption() async throws -> EncryptionStatus {
        try decode(EncryptionStatus.self, from: await request("v1/encryption", method: "DELETE"))
    }

    func writeTemporary(_ data: Data, name: String) throws -> URL {
        let folder = Self.clipFolder
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

    func makeRequest(_ path: String, method: String, body: Data? = nil, contentType: String = "application/json") -> URLRequest {
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
            request.setValue(contentType, forHTTPHeaderField: "Content-Type")
        }
        return request
    }

    private func request(_ path: String, method: String = "GET", body: Data? = nil,
                         contentType: String = "application/json") async throws -> Data {
        let (data, response) = try await transport.send(makeRequest(path, method: method, body: body, contentType: contentType))
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
