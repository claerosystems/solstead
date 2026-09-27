import SolsteadKit

/// Telemetry: minute-by-minute measurements the controller keeps for an owner-chosen period.
extension ControllerModel {
    var telemetry: TelemetryStatus? { status?.telemetry }

    /// Keeps `days` of history. Shortening deletes older days on the controller; the view confirms first.
    func setTelemetryRetention(_ days: Int) async {
        await perform {
            let client = try connected()
            _ = try await client.updateTelemetry(TelemetrySettingsUpdate(retentionDays: days))
            try await load(with: client)
            message = "Telemetry is now kept for \(Self.retentionLabel(days))."
        }
    }

    static func retentionLabel(_ days: Int) -> String {
        days == 365 ? "1 year" : "\(days) days"
    }
}
