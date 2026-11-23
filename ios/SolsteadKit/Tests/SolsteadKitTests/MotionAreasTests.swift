import XCTest
@testable import SolsteadKit

final class MotionAreasTests: XCTestCase {
    let motionJSON = Data("""
    {"zones": [{"name": "Driveway", "points": [[0.1, 0.5], [0.9, 0.5], [0.9, 1.0], [0.1, 1.0]]}],
     "ignore_shadows": true, "sensitivity": "medium", "verify": true,
     "check": {"available": true, "enabled": true, "running": true, "frames": 120, "restarts": 0,
               "last_error": null, "seeing_motion": false, "largest_object_pixels": 0,
               "shadow_fraction": 0.0, "checking": true, "confirmed": 3, "ignored": 17,
               "passed_through": 1}}
    """.utf8)

    func testDecodesAndSendsMotionSettings() async throws {
        let transport = StubTransport()
        let pairing = try Pairing(url: "https://solstead.local:8443", token: String(repeating: "k", count: 32),
                                  fingerprint: String(repeating: "cd", count: 32))
        let client = ControllerClient(pairing: pairing, transport: transport)
        transport.responses = [(200, motionJSON), (200, motionJSON)]

        var settings = try await client.motion()
        XCTAssertEqual(settings.zones.first?.name, "Driveway")
        XCTAssertEqual(settings.zones.first?.points.first, MotionPoint(x: 0.1, y: 0.5))
        XCTAssertEqual(settings.sensitivity, .medium)
        XCTAssertEqual(settings.check?.ignored, 17)
        XCTAssertEqual(settings.check?.passedThrough, 1)

        settings.ignoreShadows = false
        settings.zones.append(.rectangle(named: "Path"))
        _ = try await client.updateMotion(settings)
        let put = try XCTUnwrap(transport.requests.last)
        XCTAssertEqual(put.httpMethod, "PUT")
        XCTAssertEqual(put.url?.path, "/v1/motion")
        let sent = try XCTUnwrap(put.httpBody.flatMap { try? JSONSerialization.jsonObject(with: $0) as? [String: Any] })
        XCTAssertEqual(sent["ignore_shadows"] as? Bool, false)
        XCTAssertEqual(sent["sensitivity"] as? String, "medium")
        XCTAssertNil(sent["check"])
        let zones = try XCTUnwrap(sent["zones"] as? [[String: Any]])
        XCTAssertEqual(zones.count, 2)
        XCTAssertEqual(zones[1]["name"] as? String, "Path")
        XCTAssertEqual((zones[1]["points"] as? [[Double]])?.first, [0.3, 0.3])
        XCTAssertNil(zones[1]["id"])
    }

    func testGeometry() {
        let square = MotionArea.rectangle(named: "A", center: MotionPoint(x: 0.95, y: 0.5), width: 0.2, height: 0.2)
        XCTAssertEqual(square.points.map(\.x).max() ?? 0, 1.0, accuracy: 1e-9, "a new area is kept inside the picture")
        XCTAssertEqual(square.area, 0.04, accuracy: 1e-9)
        XCTAssertTrue(square.contains(MotionPoint(x: 0.9, y: 0.5)))
        XCTAssertFalse(square.contains(MotionPoint(x: 0.5, y: 0.5)))
        XCTAssertEqual(square.midpoint(after: 0).x, 0.9, accuracy: 1e-9)
        XCTAssertEqual(square.midpoint(after: 0).y, 0.4, accuracy: 1e-9)

        let moved = square.moved(from: square.points, dx: 0.5, dy: -0.1)
        XCTAssertEqual(moved.points.map(\.x).max() ?? 0, 1.0, accuracy: 1e-9, "moving stops at the edge")
        XCTAssertEqual(moved.points.map(\.y).min() ?? 0, 0.3, accuracy: 1e-9)
        XCTAssertEqual(MotionPoint(x: -0.2, y: 1.4).clamped, MotionPoint(x: 0, y: 1))
    }

    func testSameChoicesIgnoresCountsAndIdentity() throws {
        let a = MotionSettings(zones: [.rectangle(named: "A")])
        var b = MotionSettings(zones: [.rectangle(named: "A")])
        XCTAssertTrue(a.sameChoices(as: b))
        b.sensitivity = .high
        XCTAssertFalse(a.sameChoices(as: b))
    }
}
