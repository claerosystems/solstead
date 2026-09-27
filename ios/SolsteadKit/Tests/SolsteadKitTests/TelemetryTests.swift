import XCTest
@testable import SolsteadKit

final class TelemetryTests: XCTestCase {
    var transport = StubTransport()
    var client: ControllerClient!

    override func setUpWithError() throws {
        transport = StubTransport()
        let pairing = try Pairing(url: "https://solstead.local:8443", token: String(repeating: "k", count: 32),
                                  fingerprint: String(repeating: "cd", count: 32))
        client = ControllerClient(pairing: pairing, transport: transport)
    }

    func testHistoryRequestAndDecoding() async throws {
        transport.responses = [(200, Data("""
        {"range": "7d", "since_unix": 1790000000, "until_unix": 1790600000, "bucket_seconds": 1800,
         "retention_days": 30, "points": [
           {"unix": 1790001800, "minutes": 30, "cpu_c": 51.25, "cpu_c_low": 49.0, "cpu_c_high": 55.5,
            "undervoltage": 0, "throttled": 2},
           {"unix": 1790003600, "minutes": 4}]}
        """.utf8))]
        let history = try await client.telemetry(range: .week)
        XCTAssertEqual(transport.requests.first?.url?.absoluteString, "https://solstead.local:8443/v1/telemetry?range=7d")
        XCTAssertEqual(history.bucketSeconds, 1800)
        XCTAssertEqual(history.points.first?.cpuC, 51.25)
        XCTAssertEqual(history.points.first?.cpuCHigh, 55.5)
        XCTAssertEqual(history.points.first?.throttled, 2)
        XCTAssertNil(history.points.last?.cpuC)
    }

    func testUpdateRetentionSendsSnakeCase() async throws {
        transport.responses = [(200, Data("""
        {"retention_days": 90, "retention_choices": [7, 30, 90, 365], "clock_synchronized": true,
         "held_readings": 0, "pending_minutes": 0, "last_sample_unix": 1790000000, "fields": ["cpu_c"]}
        """.utf8))]
        let status = try await client.updateTelemetry(TelemetrySettingsUpdate(retentionDays: 90))
        XCTAssertEqual(status.retentionDays, 90)
        XCTAssertTrue(status.clockSynchronized)
        let request = try XCTUnwrap(transport.requests.first)
        XCTAssertEqual(request.httpMethod, "PUT")
        XCTAssertEqual(String(data: try XCTUnwrap(request.httpBody), encoding: .utf8), #"{"retention_days":90}"#)
    }

    func testSegmentsBreakAtMissingBuckets() {
        func point(_ unix: Int, _ cpu: Double?) -> TelemetryPoint {
            TelemetryPoint(unix: unix, minutes: 5, cpuC: cpu, cpuCLow: cpu, cpuCHigh: cpu, undervoltage: nil, throttled: nil)
        }
        let history = TelemetryHistory(range: "24h", sinceUnix: 0, untilUnix: 3000, bucketSeconds: 300, retentionDays: 30,
                                       points: [point(0, 50), point(300, 51), point(900, 52), point(1200, nil), point(1500, 53)])
        let runs = history.segments { $0.cpuC }
        XCTAssertEqual(runs.map { $0.map(\.unix) }, [[0, 300], [900], [1500]])
    }
}
