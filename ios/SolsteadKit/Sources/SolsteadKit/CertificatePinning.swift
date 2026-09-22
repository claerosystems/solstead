import CryptoKit
import Foundation
import Security

public enum CertificatePinning {
    /// Lower-case hex SHA-256 of a DER-encoded certificate; matches `solstead fingerprint`.
    public static func fingerprint(ofDER der: Data) -> String {
        SHA256.hash(data: der).map { String(format: "%02x", $0) }.joined()
    }

    /// Accepts "AB:CD:…", "abcd…", with whitespace; returns 64 lower-case hex digits or nil.
    public static func normalize(_ raw: String) -> String? {
        let cleaned = raw.lowercased().filter { !$0.isWhitespace && $0 != ":" }
        guard cleaned.count == 64, cleaned.allSatisfy({ $0.isHexDigit && $0.isASCII }) else { return nil }
        return cleaned
    }
}

/// Accepts exactly one server certificate: the one whose SHA-256 fingerprint the owner entered.
/// The controller's certificate is self-signed, so the system trust store is never consulted.
public final class PinnedTrustDelegate: NSObject, URLSessionDelegate, @unchecked Sendable {
    public let pairing: Pairing

    public init(pairing: Pairing) { self.pairing = pairing }

    public func urlSession(_ session: URLSession, didReceive challenge: URLAuthenticationChallenge,
                           completionHandler: @escaping (URLSession.AuthChallengeDisposition, URLCredential?) -> Void) {
        let space = challenge.protectionSpace
        guard space.authenticationMethod == NSURLAuthenticationMethodServerTrust,
              space.host.lowercased() == pairing.host.lowercased(),
              let trust = space.serverTrust,
              let chain = SecTrustCopyCertificateChain(trust) as? [SecCertificate],
              let leaf = chain.first,
              CertificatePinning.fingerprint(ofDER: SecCertificateCopyData(leaf) as Data) == pairing.fingerprint else {
            completionHandler(.cancelAuthenticationChallenge, nil)
            return
        }
        completionHandler(.useCredential, URLCredential(trust: trust))
    }
}
