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

    func testChargerFieldsAndDailyEnergy() async throws {
        transport.responses = [(200, Data("""
        {"range": "24h", "since_unix": 1790000000, "until_unix": 1790086400, "bucket_seconds": 300,
         "retention_days": 30, "points": [
           {"unix": 1790000300, "minutes": 5, "battery_v": 12.78, "battery_v_low": 12.7, "battery_v_high": 12.8,
            "battery_v_min": 12.62, "battery_a": -0.6, "solar_w": 41.5, "solar_w_low": 30, "solar_w_high": 52,
            "load_w": 7.7, "load_w_low": 7.6, "load_w_high": 7.9, "solar_wh": 3.46, "load_wh": 0.64,
            "charge_state": "bulk"}],
         "energy": [{"date": "2026-11-08", "solar_wh": 212.4, "load_wh": 180.2}]}
        """.utf8))]
        let history = try await client.telemetry(range: .day)
        let point = try XCTUnwrap(history.points.first)
        XCTAssertEqual(point.batteryVMin, 12.62)
        XCTAssertEqual(point.solarWHigh, 52)
        XCTAssertEqual(point.loadWh, 0.64)
        XCTAssertEqual(point.chargeState, "bulk")
        XCTAssertEqual(history.energy?.first?.solarWh, 212.4)
        XCTAssertEqual(Calendar.current.component(.day, from: try XCTUnwrap(history.energy?.first?.day)), 8)
    }

    func testChargerScanChooseAndForget() async throws {
        transport.responses = [
            (200, Data("""
            {"configured": false, "address": null, "name": null, "scanning": true, "error": null,
             "key_mismatch": false, "last_heard_unix": null, "reading": null,
             "seen": [{"address": "F9:C2:B8:CB:0A:F5", "name": "SmartSolar HQ2607", "rssi": -54,
                       "last_heard_unix": 1794143186, "solar_charger": true, "chosen": false}]}
            """.utf8)),
            (200, Data("""
            {"configured": true, "address": "F9:C2:B8:CB:0A:F5", "name": "SmartSolar HQ2607", "scanning": true,
             "error": null, "key_mismatch": false, "last_heard_unix": 1794143194,
             "reading": {"unix": 1794143194.04, "state": "off", "error": 0, "battery_v": 12.79, "battery_a": -0.6,
                         "solar_w": 0, "load_a": 0.6, "yield_today_wh": 0, "load_w": 7.7},
             "seen": []}
            """.utf8)),
            (400, Data(#"{"error": "That key does not match this charger"}"#.utf8)),
        ]
        let scan = try await client.charger()
        XCTAssertEqual(scan.seen?.first?.name, "SmartSolar HQ2607")
        let chosen = try await client.chooseCharger(ChargerChoice(address: "F9:C2:B8:CB:0A:F5", key: String(repeating: "a", count: 32)))
        XCTAssertEqual(chosen.reading?.batteryV, 12.79)
        XCTAssertEqual(chosen.reading?.loadW, 7.7)
        let put = try XCTUnwrap(transport.requests.last)
        XCTAssertEqual(put.httpMethod, "PUT")
        XCTAssertEqual(put.url?.absoluteString, "https://solstead.local:8443/v1/charger")
        do {
            _ = try await client.chooseCharger(ChargerChoice(address: "F9:C2:B8:CB:0A:F5", key: "bad"))
            XCTFail("expected an error")
        } catch ControllerError.controller(let message, let status) {
            XCTAssertEqual(status, 400)
            XCTAssertTrue(message.contains("does not match"))
        }
    }

    func testSegmentsBreakAtMissingBuckets() {
        func point(_ unix: Int, _ cpu: Double?) -> TelemetryPoint {
            let json = cpu.map { #"{"unix": \#(unix), "minutes": 5, "cpu_c": \#($0)}"# } ?? #"{"unix": \#(unix), "minutes": 5}"#
            return try! ControllerJSON.decoder.decode(TelemetryPoint.self, from: Data(json.utf8))
        }
        let history = TelemetryHistory(range: "24h", sinceUnix: 0, untilUnix: 3000, bucketSeconds: 300, retentionDays: 30,
                                       points: [point(0, 50), point(300, 51), point(900, 52), point(1200, nil), point(1500, 53)],
                                       energy: nil)
        let runs = history.segments { $0.cpuC }
        XCTAssertEqual(runs.map { $0.map(\.unix) }, [[0, 300], [900], [1500]])
    }
}
