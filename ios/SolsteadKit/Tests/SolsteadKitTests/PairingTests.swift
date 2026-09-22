import XCTest
@testable import SolsteadKit

final class PairingTests: XCTestCase {
    let token = String(repeating: "t", count: 40)
    let fingerprint = String(repeating: "ab", count: 32)

    func testValidPairing() throws {
        let pairing = try Pairing(url: " https://solstead-r4b-1.local:8443 ", token: token, fingerprint: fingerprint)
        XCTAssertEqual(pairing.host, "solstead-r4b-1.local")
        XCTAssertEqual(pairing.url.port, 8443)
        XCTAssertEqual(pairing.fingerprint, fingerprint)
    }

    func testFingerprintIsNormalized() throws {
        let colons = stride(from: 0, to: 64, by: 2).map { i -> String in
            let start = fingerprint.index(fingerprint.startIndex, offsetBy: i)
            return String(fingerprint[start..<fingerprint.index(start, offsetBy: 2)]).uppercased()
        }.joined(separator: ":")
        let pairing = try Pairing(url: "https://h", token: token, fingerprint: colons)
        XCTAssertEqual(pairing.fingerprint, fingerprint)
    }

    func testRejectsInsecureOrDecoratedURLs() {
        for bad in ["http://h:8443", "https://user:pw@h", "https://h/?x=1", "https://h/#f", "https://h/api", "", "not a url"] {
            XCTAssertThrowsError(try Pairing(url: bad, token: token, fingerprint: fingerprint), bad) {
                XCTAssertEqual($0 as? PairingError, .invalidURL, bad)
            }
        }
    }

    func testRejectsShortToken() {
        XCTAssertThrowsError(try Pairing(url: "https://h", token: "short", fingerprint: fingerprint)) {
            XCTAssertEqual($0 as? PairingError, .invalidToken)
        }
    }

    func testRejectsBadFingerprint() {
        for bad in ["", "abcd", String(repeating: "zz", count: 32), fingerprint + "0"] {
            XCTAssertThrowsError(try Pairing(url: "https://h", token: token, fingerprint: bad)) {
                XCTAssertEqual($0 as? PairingError, .invalidFingerprint)
            }
        }
    }

    func testRoundTripsThroughJSON() throws {
        let pairing = try Pairing(url: "https://h:1", token: token, fingerprint: fingerprint)
        let data = try JSONEncoder().encode(pairing)
        XCTAssertEqual(try JSONDecoder().decode(Pairing.self, from: data), pairing)
    }
}
