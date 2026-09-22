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

    public func snapshot() async throws -> Data {
        try await request("v1/camera/snapshot")
    }

    @discardableResult
    public func record() async throws -> Recording {
        try decode(Recording.self, from: await request("v1/recordings", method: "POST"))
    }

    // MARK: - Internals

    func makeRequest(_ path: String, method: String) -> URLRequest {
        var request = URLRequest(url: pairing.url.appendingPathComponent(path))
        request.httpMethod = method
        request.setValue("Bearer " + pairing.token, forHTTPHeaderField: "Authorization")
        request.setValue("application/json, image/jpeg", forHTTPHeaderField: "Accept")
        return request
    }

    private func request(_ path: String, method: String = "GET") async throws -> Data {
        let (data, response) = try await transport.send(makeRequest(path, method: method))
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
