import Foundation
import Security
import SolsteadKit

/// Keeps the owner's pairing in the Keychain, on this device only, never in iCloud backups.
enum PairingStore {
    static let service = "com.claerosystems.solstead.controller"
    static let account = "owner"

    enum StoreError: LocalizedError {
        case status(OSStatus)
        var errorDescription: String? { "Could not save the pairing in the Keychain (\(status))." }
        private var status: OSStatus { if case let .status(code) = self { return code }; return 0 }
    }

    private static var query: [String: Any] {
        [kSecClass as String: kSecClassGenericPassword,
         kSecAttrService as String: service,
         kSecAttrAccount as String: account]
    }

    static func save(_ pairing: Pairing) throws {
        let data = try JSONEncoder().encode(pairing)
        let update = SecItemUpdate(query as CFDictionary, [kSecValueData as String: data] as CFDictionary)
        if update == errSecSuccess { return }
        guard update == errSecItemNotFound else { throw StoreError.status(update) }
        var item = query
        item[kSecValueData as String] = data
        item[kSecAttrAccessible as String] = kSecAttrAccessibleWhenUnlockedThisDeviceOnly
        let add = SecItemAdd(item as CFDictionary, nil)
        guard add == errSecSuccess else { throw StoreError.status(add) }
    }

    static func load() -> Pairing? {
        var item = query
        item[kSecReturnData as String] = true
        var result: CFTypeRef?
        guard SecItemCopyMatching(item as CFDictionary, &result) == errSecSuccess,
              let data = result as? Data else { return nil }
        return try? JSONDecoder().decode(Pairing.self, from: data)
    }

    static func clear() {
        SecItemDelete(query as CFDictionary)
    }
}
