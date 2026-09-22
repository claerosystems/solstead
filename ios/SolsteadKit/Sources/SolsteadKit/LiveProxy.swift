import Foundation
import Network

/// A loopback HTTP server that hands the controller's live HLS stream to AVPlayer.
///
/// AVPlayer fetches HLS media segments with its own networking, which cannot use our pinned
/// session or bearer token. So the player talks plain HTTP to 127.0.0.1 and this proxy fetches
/// each playlist and segment through `ControllerClient`. The listener binds to loopback on a
/// random port and every path carries a random secret, so nothing else on the device can use it.
public final class LiveProxy: @unchecked Sendable {
    public let client: ControllerClient
    public let secret = UUID().uuidString.replacingOccurrences(of: "-", with: "")
    private let listener: NWListener
    private let queue = DispatchQueue(label: "com.claerosystems.solstead.liveproxy")
    private var connections: [ObjectIdentifier: NWConnection] = [:]

    public private(set) var port: UInt16 = 0

    public init(client: ControllerClient) throws {
        self.client = client
        let parameters = NWParameters.tcp
        parameters.requiredInterfaceType = .loopback
        parameters.allowLocalEndpointReuse = true
        listener = try NWListener(using: parameters, on: .any)
    }

    /// Starts listening and returns once a port is assigned.
    public func start() async throws {
        try await withCheckedThrowingContinuation { (continuation: CheckedContinuation<Void, Error>) in
            var resumed = false
            listener.stateUpdateHandler = { [weak self] state in
                guard let self, !resumed else { return }
                switch state {
                case .ready:
                    resumed = true
                    self.port = self.listener.port?.rawValue ?? 0
                    continuation.resume()
                case let .failed(error):
                    resumed = true
                    continuation.resume(throwing: error)
                default:
                    break
                }
            }
            listener.newConnectionHandler = { [weak self] connection in self?.accept(connection) }
            listener.start(queue: queue)
        }
    }

    public func stop() {
        listener.cancel()
        queue.async {
            self.connections.values.forEach { $0.cancel() }
            self.connections.removeAll()
        }
    }

    /// The playlist URL to give AVPlayer. Segment names in the playlist are relative, so they
    /// resolve back to this proxy without rewriting.
    public var playlistURL: URL {
        URL(string: "http://127.0.0.1:\(port)/\(secret)/index.m3u8")!
    }

    // MARK: - Request handling

    private func accept(_ connection: NWConnection) {
        connections[ObjectIdentifier(connection)] = connection
        connection.stateUpdateHandler = { [weak self, weak connection] state in
            if case .cancelled = state, let connection { self?.connections[ObjectIdentifier(connection)] = nil }
            if case .failed = state, let connection { self?.connections[ObjectIdentifier(connection)] = nil }
        }
        connection.start(queue: queue)
        readRequest(on: connection, buffer: Data())
    }

    private func readRequest(on connection: NWConnection, buffer: Data) {
        connection.receive(minimumIncompleteLength: 1, maximumLength: 16 * 1024) { [weak self] data, _, isComplete, error in
            guard let self else { return }
            var buffer = buffer
            if let data { buffer.append(data) }
            if let range = buffer.range(of: Data("\r\n\r\n".utf8)) {
                let head = String(decoding: buffer[..<range.lowerBound], as: UTF8.self)
                self.handle(head: head, on: connection)
            } else if error != nil || isComplete || buffer.count > 16 * 1024 {
                connection.cancel()
            } else {
                self.readRequest(on: connection, buffer: buffer)
            }
        }
    }

    static func parse(requestHead: String, secret: String) -> String? {
        let parts = requestHead.split(separator: "\r\n").first?.split(separator: " ") ?? []
        guard parts.count >= 2, parts[0] == "GET" || parts[0] == "HEAD" else { return nil }
        let path = String(parts[1]).split(separator: "?").first.map(String.init) ?? ""
        let components = path.split(separator: "/").map(String.init)
        guard components.count == 2, components[0] == secret else { return nil }
        let name = components[1]
        guard name.range(of: #"^(index\.m3u8|init\.mp4|seg_\d{5}\.m4s)$"#, options: .regularExpression) != nil else {
            return nil
        }
        return name
    }

    private func handle(head: String, on connection: NWConnection) {
        guard let name = Self.parse(requestHead: head, secret: secret) else {
            respond(on: connection, status: "404 Not Found", contentType: "text/plain", body: Data("not found".utf8))
            return
        }
        Task {
            do {
                let data = try await client.liveResource(name)
                let type = name.hasSuffix(".m3u8") ? "application/vnd.apple.mpegurl"
                    : name.hasSuffix(".mp4") ? "video/mp4" : "video/iso.segment"
                respond(on: connection, status: "200 OK", contentType: type, body: data)
            } catch {
                respond(on: connection, status: "502 Bad Gateway", contentType: "text/plain",
                        body: Data(error.localizedDescription.utf8))
            }
        }
    }

    private func respond(on connection: NWConnection, status: String, contentType: String, body: Data) {
        var response = Data("HTTP/1.1 \(status)\r\nContent-Type: \(contentType)\r\nContent-Length: \(body.count)\r\nCache-Control: no-store\r\nConnection: close\r\n\r\n".utf8)
        response.append(body)
        connection.send(content: response, completion: .contentProcessed { _ in connection.cancel() })
    }
}
