import XCTest
@testable import SolsteadKit

final class LiveProxyTests: XCTestCase {
    func testParseAcceptsOnlySecretPathsAndKnownNames() {
        let secret = "abc123"
        XCTAssertEqual(LiveProxy.parse(requestHead: "GET /abc123/index.m3u8 HTTP/1.1\r\nHost: x", secret: secret), "index.m3u8")
        XCTAssertEqual(LiveProxy.parse(requestHead: "GET /abc123/seg_00042.m4s?x=1 HTTP/1.1", secret: secret), "seg_00042.m4s")
        XCTAssertNil(LiveProxy.parse(requestHead: "GET /wrong/index.m3u8 HTTP/1.1", secret: secret))
        XCTAssertNil(LiveProxy.parse(requestHead: "GET /abc123/../etc/passwd HTTP/1.1", secret: secret))
        XCTAssertNil(LiveProxy.parse(requestHead: "POST /abc123/index.m3u8 HTTP/1.1", secret: secret))
        XCTAssertNil(LiveProxy.parse(requestHead: "GET /abc123/other.txt HTTP/1.1", secret: secret))
    }

    func testProxyServesResourcesThroughTheClient() async throws {
        let transport = StubTransport()
        transport.responses = [
            (200, Data("#EXTM3U\nseg_00001.m4s\n".utf8)),
            (200, Data([0, 0, 0, 1])),
            (503, Data(#"{"error": "Live view is not running"}"#.utf8)),
        ]
        let pairing = try Pairing(url: "https://h:8443", token: String(repeating: "k", count: 32),
                                  fingerprint: String(repeating: "ab", count: 32))
        let proxy = try LiveProxy(client: ControllerClient(pairing: pairing, transport: transport))
        try await proxy.start()
        defer { proxy.stop() }
        XCTAssertNotEqual(proxy.port, 0)
        XCTAssertTrue(proxy.playlistURL.absoluteString.hasPrefix("http://127.0.0.1:\(proxy.port)/"))

        let session = URLSession(configuration: .ephemeral)
        let (playlist, response) = try await session.data(from: proxy.playlistURL)
        XCTAssertEqual((response as? HTTPURLResponse)?.statusCode, 200)
        XCTAssertEqual((response as? HTTPURLResponse)?.mimeType, "application/vnd.apple.mpegurl")
        XCTAssertEqual(String(decoding: playlist, as: UTF8.self), "#EXTM3U\nseg_00001.m4s\n")
        XCTAssertEqual(transport.requests[0].url?.path, "/v1/live/index.m3u8")

        let segmentURL = URL(string: "seg_00001.m4s", relativeTo: proxy.playlistURL)!
        let (segment, segmentResponse) = try await session.data(from: segmentURL)
        XCTAssertEqual(segment, Data([0, 0, 0, 1]))
        XCTAssertEqual((segmentResponse as? HTTPURLResponse)?.mimeType, "video/iso.segment")

        let (_, failed) = try await session.data(from: URL(string: "init.mp4", relativeTo: proxy.playlistURL)!)
        XCTAssertEqual((failed as? HTTPURLResponse)?.statusCode, 502)

        let (_, wrongSecret) = try await session.data(from: URL(string: "http://127.0.0.1:\(proxy.port)/nope/index.m3u8")!)
        XCTAssertEqual((wrongSecret as? HTTPURLResponse)?.statusCode, 404)
    }
}
