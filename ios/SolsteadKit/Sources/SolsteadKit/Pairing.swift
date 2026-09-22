import Foundation

/// Everything the phone needs to reach one controller: where it is, the owner token,
/// and the SHA-256 fingerprint of the controller's self-signed certificate.
public struct Pairing: Codable, Equatable, Sendable {
    public static let minimumTokenLength = 32

    public let url: URL
    public let token: String
    public let fingerprint: String

    /// Validates owner-entered values. `fingerprint` accepts upper/lower case and colon separators.
    public init(url rawURL: String, token: String, fingerprint rawFingerprint: String) throws {
        let trimmed = rawURL.trimmingCharacters(in: .whitespacesAndNewlines)
        guard let url = URL(string: trimmed), url.scheme?.lowercased() == "https",
              let host = url.host, !host.isEmpty,
              url.user == nil, url.password == nil, url.query == nil, url.fragment == nil,
              url.path.isEmpty || url.path == "/" else {
            throw PairingError.invalidURL
        }
        guard let fingerprint = CertificatePinning.normalize(rawFingerprint) else {
            throw PairingError.invalidFingerprint
        }
        let token = token.trimmingCharacters(in: .whitespacesAndNewlines)
        guard token.count >= Self.minimumTokenLength else { throw PairingError.invalidToken }
        self.url = url
        self.token = token
        self.fingerprint = fingerprint
    }

    public var host: String { url.host ?? "" }
}

public enum PairingError: LocalizedError, Equatable {
    case invalidURL, invalidToken, invalidFingerprint

    public var errorDescription: String? {
        switch self {
        case .invalidURL:
            return "Enter the controller's HTTPS address, for example https://solstead-r4b-1.local:8443."
        case .invalidToken:
            return "The owner access token must be at least \(Pairing.minimumTokenLength) characters."
        case .invalidFingerprint:
            return "Enter the 64-character SHA-256 certificate fingerprint shown when the controller was provisioned."
        }
    }
}
