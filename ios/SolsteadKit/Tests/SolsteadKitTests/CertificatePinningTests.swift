import XCTest
@testable import SolsteadKit

final class CertificatePinningTests: XCTestCase {
    func testFingerprintMatchesKnownVector() {
        XCTAssertEqual(CertificatePinning.fingerprint(ofDER: Data()),
                       "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855")
        XCTAssertEqual(CertificatePinning.fingerprint(ofDER: Data("abc".utf8)),
                       "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad")
    }

    func testNormalize() {
        let hex = String(repeating: "0f", count: 32)
        XCTAssertEqual(CertificatePinning.normalize(hex.uppercased()), hex)
        XCTAssertEqual(CertificatePinning.normalize(" " + hex + "\n"), hex)
        XCTAssertNil(CertificatePinning.normalize(String(hex.dropLast())))
        XCTAssertNil(CertificatePinning.normalize(hex.replacingOccurrences(of: "0f", with: "0g")))
    }
}
