import SolsteadKit

/// Which link, Wi-Fi or cellular, the controller prefers for reaching the internet.
extension ControllerModel {
    var network: NetworkStatus? { status?.network }

    /// Asks the controller to prefer `preference`. It keeps both links up and still falls back to
    /// the other one when the preferred link has no internet.
    func setPreferredUplink(_ preference: NetworkStatus.Preference) async {
        await perform {
            let client = try connected()
            let network = try await client.setPreferredUplink(preference)
            try await load(with: client)
            message = Self.preferenceMessage(preference, now: status?.network ?? network)
        }
    }

    static func preferenceMessage(_ preference: NetworkStatus.Preference, now network: NetworkStatus) -> String {
        let wanted = preference == .automatic
            ? "Controller set to Automatic (Wi-Fi first)"
            : "Controller prefers \(preference.name)"
        if let note = network.fallbackNote { return "\(wanted). \(note)" }
        return "\(wanted); now on \(network.uplinkName)."
    }
}
