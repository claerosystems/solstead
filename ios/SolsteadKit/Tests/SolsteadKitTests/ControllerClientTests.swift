import XCTest
@testable import SolsteadKit

/// Serves canned responses and records what the client sent.
final class StubTransport: ControllerTransport, @unchecked Sendable {
    var responses: [(status: Int, body: Data)] = []
    var requests: [URLRequest] = []

    func send(_ request: URLRequest) async throws -> (Data, URLResponse) {
        requests.append(request)
        let next = responses.isEmpty ? (status: 500, body: Data()) : responses.removeFirst()
        let response = HTTPURLResponse(url: request.url!, statusCode: next.status, httpVersion: "HTTP/1.1", headerFields: nil)!
        return (next.body, response)
    }
}

final class ControllerClientTests: XCTestCase {
    let statusJSON = Data("""
    {"name": "Solstead", "version": "0.1.0", "uptime_seconds": 61, "temperature_c": 41.5,
     "storage": {"ready": true, "total_bytes": 1000, "free_bytes": 400, "used_bytes": 600},
     "camera": {"configured": true, "last_success_unix": 1758500000},
     "recording_mode": "manual", "remote_access": false}
    """.utf8)

    var transport = StubTransport()
    var client: ControllerClient!

    override func setUpWithError() throws {
        transport = StubTransport()
        let pairing = try Pairing(url: "https://solstead.local:8443", token: String(repeating: "k", count: 32),
                                  fingerprint: String(repeating: "cd", count: 32))
        client = ControllerClient(pairing: pairing, transport: transport)
    }

    func testStatusRequestAndDecoding() async throws {
        transport.responses = [(200, statusJSON)]
        let status = try await client.status()
        XCTAssertEqual(status.name, "Solstead")
        XCTAssertEqual(status.uptimeSeconds, 61)
        XCTAssertEqual(status.temperatureC, 41.5)
        XCTAssertEqual(status.storage.freeBytes, 400)
        XCTAssertEqual(status.camera.lastSuccessUnix, 1758500000)
        let request = try XCTUnwrap(transport.requests.first)
        XCTAssertEqual(request.url?.absoluteString, "https://solstead.local:8443/v1/status")
        XCTAssertEqual(request.httpMethod, "GET")
        XCTAssertEqual(request.value(forHTTPHeaderField: "Authorization"), "Bearer " + String(repeating: "k", count: 32))
    }

    func testStatusWithUnavailableStorage() async throws {
        let json = Data("""
        {"name": "S", "version": "0.1.0", "uptime_seconds": 1, "temperature_c": null,
         "storage": {"ready": false, "error": "Recording SSD is not mounted"},
         "camera": {"configured": false, "last_success_unix": null},
         "recording_mode": "manual", "remote_access": false}
        """.utf8)
        transport.responses = [(200, json)]
        let status = try await client.status()
        XCTAssertFalse(status.storage.ready)
        XCTAssertEqual(status.storage.error, "Recording SSD is not mounted")
        XCTAssertNil(status.temperatureC)
    }

    func testRecordingsAndRecord() async throws {
        transport.responses = [
            (201, Data(#"{"id": "a.mp4", "size_bytes": 12, "created_unix": 1758500001}"#.utf8)),
            (200, Data(#"{"recordings": [{"id": "a.mp4", "size_bytes": 12, "created_unix": 1758500001}]}"#.utf8)),
        ]
        let clip = try await client.record()
        XCTAssertEqual(clip.id, "a.mp4")
        XCTAssertEqual(clip.created, Date(timeIntervalSince1970: 1758500001))
        XCTAssertEqual(transport.requests[0].httpMethod, "POST")
        XCTAssertEqual(transport.requests[0].url?.path, "/v1/recordings")
        let listed = try await client.recordings()
        XCTAssertEqual(listed, [clip])
    }

    func testSnapshotReturnsRawBytes() async throws {
        transport.responses = [(200, Data([0xFF, 0xD8, 0x01]))]
        let data = try await client.snapshot()
        XCTAssertEqual(data, Data([0xFF, 0xD8, 0x01]))
        XCTAssertEqual(transport.requests[0].url?.path, "/v1/camera/snapshot")
    }

    func testControllerErrorMessageIsSurfaced() async {
        transport.responses = [(503, Data(#"{"error": "Camera capture is busy; try again shortly"}"#.utf8))]
        do {
            _ = try await client.snapshot()
            XCTFail("expected error")
        } catch {
            XCTAssertEqual(error as? ControllerError, .controller("Camera capture is busy; try again shortly", status: 503))
            XCTAssertEqual(error.localizedDescription, "Camera capture is busy; try again shortly")
        }
    }

    func testUnexpectedStatusWithoutBody() async {
        transport.responses = [(502, Data("<html>".utf8))]
        do {
            _ = try await client.status()
            XCTFail("expected error")
        } catch {
            XCTAssertEqual(error as? ControllerError, .unexpectedResponse(status: 502))
        }
    }
}
