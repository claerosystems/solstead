import XCTest
@testable import SolsteadKit

final class NetworkStatusTests: XCTestCase {
    func testDecodesControllerJSON() throws {
        let json = Data("""
        {"uplink": "cellular", "interface": "wwan0",
         "wifi": {"interface": "wlan0", "signal_percent": 64, "signal_dbm": -61},
         "cellular": {"signal_percent": 83, "technology": "lte", "operator": "TELUS", "state": "connected"}}
        """.utf8)
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        let network = try decoder.decode(NetworkStatus.self, from: json)
        XCTAssertEqual(network.uplink, .cellular)
        XCTAssertEqual(network.uplinkName, "Cellular (LTE)")
        XCTAssertEqual(network.uplinkSignalPercent, 83)
        XCTAssertEqual(network.symbolName, "cellularbars")
        XCTAssertEqual(network.wifi?.signalDbm, -61)
        XCTAssertEqual(network.cellular?.operator, "TELUS")
    }

    func testNoRouteAndWiredLinks() throws {
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        let none = try decoder.decode(NetworkStatus.self, from: Data(#"{"uplink": null, "interface": null, "wifi": null, "cellular": null}"#.utf8))
        XCTAssertEqual(none.uplinkName, "No internet")
        XCTAssertNil(none.uplinkSignalPercent)
        XCTAssertEqual(NetworkStatus(uplink: .ethernet, interface: "eth0").uplinkSignalPercent, nil)
    }

    func testStrengthWords() {
        XCTAssertEqual(NetworkStatus.strength(83), "Strong")
        XCTAssertEqual(NetworkStatus.strength(55), "Good")
        XCTAssertEqual(NetworkStatus.strength(30), "Fair")
        XCTAssertEqual(NetworkStatus.strength(12), "Weak")
        XCTAssertEqual(NetworkStatus.describe(percent: 83, dbm: -52), "Strong, 83% (-52 dBm)")
        XCTAssertEqual(NetworkStatus.describe(percent: nil), "No reading")
    }

    func testOlderControllersHaveNoPreference() throws {
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        let network = try decoder.decode(NetworkStatus.self, from: Data(#"{"uplink": "wifi", "interface": "wlan0"}"#.utf8))
        XCTAssertNil(network.preferred)
        XCTAssertFalse(network.supportsPreference)
        XCTAssertFalse(network.fallback)
        XCTAssertNil(network.fallbackNote)
    }

    func testDecodesPreferenceAndFallback() throws {
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        let network = try decoder.decode(NetworkStatus.self, from: Data("""
        {"uplink": "cellular", "interface": "wwan0", "preferred": "wifi", "fallback": true,
         "fallback_reason": "Wi-Fi has no internet",
         "cellular": {"signal_percent": 70, "technology": "lte", "operator": null, "state": "connected"}}
        """.utf8))
        XCTAssertEqual(network.preferred, .wifi)
        XCTAssertTrue(network.supportsPreference)
        XCTAssertEqual(network.fallbackNote, "Using Cellular (LTE): Wi-Fi has no internet.")

        let unexplained = NetworkStatus(uplink: .wifi, preferred: .cellular, fallback: true)
        XCTAssertEqual(unexplained.fallbackNote, "Using Wi-Fi: Cellular isn't working.")
        XCTAssertNil(NetworkStatus(uplink: nil, preferred: .wifi, fallback: true).fallbackNote)
    }

    func testUnknownValuesDoNotFailTheStatus() throws {
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        let network = try decoder.decode(NetworkStatus.self, from: Data(#"{"uplink": "satellite", "interface": "sat0", "preferred": "satellite"}"#.utf8))
        XCTAssertEqual(network.uplink, .other)
        XCTAssertNil(network.preferred)
    }
}
