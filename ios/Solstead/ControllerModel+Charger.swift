import SolsteadKit

/// The Victron solar charger the controller reads over Bluetooth for battery and solar telemetry.
extension ControllerModel {
    var charger: ChargerStatus? { status?.charger }

    /// Saves the picked charger and its key on the controller. Throws so the picker can stay open
    /// and show what was wrong, typically a key that doesn't match.
    func chooseCharger(address: String, key: String) async throws {
        let client = try connected()
        let chosen = try await client.chooseCharger(ChargerChoice(address: address, key: key))
        try await load(with: client)
        message = "Reading solar charger \(chosen.name ?? address)."
    }

    func forgetCharger() async {
        await perform {
            let client = try connected()
            _ = try await client.forgetCharger()
            try await load(with: client)
            message = "Solar charger removed; its key was deleted from the controller."
        }
    }
}
