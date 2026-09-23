import Foundation
import Security
import SolsteadKit

/// This phone's device key (ADR 0003): opens recordings sealed to it. Kept in the Keychain on
/// this device only, never synced or backed up, and kept when a pairing is forgotten so older
/// footage stays viewable. Only its public half ever leaves the phone.
enum DeviceKeyStore {
    static let service = "com.claerosystems.solstead.device-key"
    static let account = "x25519"

    private static var query: [String: Any] {
        [kSecClass as String: kSecClassGenericPassword,
         kSecAttrService as String: service,
         kSecAttrAccount as String: account]
    }

    static func load() -> AgeIdentity? {
        var item = query
        item[kSecReturnData as String] = true
        var result: CFTypeRef?
        guard SecItemCopyMatching(item as CFDictionary, &result) == errSecSuccess,
              let data = result as? Data else { return nil }
        return try? AgeIdentity(rawRepresentation: data)
    }

    /// The existing key, or a new one saved before it is returned.
    static func loadOrCreate() throws -> AgeIdentity {
        if let existing = load() { return existing }
        let identity = AgeIdentity()
        var item = query
        item[kSecValueData as String] = identity.rawRepresentation
        item[kSecAttrAccessible as String] = kSecAttrAccessibleWhenUnlockedThisDeviceOnly
        let status = SecItemAdd(item as CFDictionary, nil)
        guard status == errSecSuccess else {
            throw ControllerError.controller("Could not save this phone's key in the Keychain (\(status)).", status: 0)
        }
        return identity
    }
}
