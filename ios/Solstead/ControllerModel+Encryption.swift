import SolsteadKit
import UIKit

/// Encryption at rest (ADR 0003): the owner's switch, key setup and enrolment.
extension ControllerModel {
    var encryption: EncryptionStatus? { status?.encryption }

    /// Whether recordings sealed from now on can be opened by this phone.
    var thisPhoneEnrolled: Bool {
        guard let mine = DeviceKeyStore.load()?.recipient else { return false }
        return encryption?.deviceKeys.contains { $0.publicKey == mine } ?? false
    }

    /// Starts setup: a fresh recovery key the owner must print or save, then type back.
    func beginEncryptionSetup() {
        recoverySetup = RecoveryKey()
    }

    func cancelEncryptionSetup() {
        recoverySetup = nil
    }

    /// Enrols this phone's key and the recovery key; other phones already enrolled keep access.
    func finishEncryptionSetup(_ recovery: RecoveryKey) async {
        await perform {
            let device = try DeviceKeyStore.loadOrCreate()
            let current = try await connected().encryption()
            _ = try await connected().enroll(current.enrolment(adding: (UIDevice.current.name, device.recipient),
                                                               recovery: recovery.recipient))
            try await load(with: try connected())
            recoverySetup = nil
            message = "Encryption is on. New recordings can only be opened with this phone or your recovery key."
        }
    }

    /// The public half of the recovery key new recordings are sealed to.
    var recoveryPublicKey: String? { encryption?.recoveryKey?.publicKey }

    /// Restores access on this phone with the owner's recovery key (SolsteadKit's AccessRestorer).
    /// The recovery key is used here and not kept. Returns true when finished.
    func restoreAccess(with recovery: AgeIdentity) async -> Bool {
        var finished = false
        await perform {
            let client = try connected()
            let restorer = AccessRestorer(client: client, recovery: recovery,
                                          device: try DeviceKeyStore.loadOrCreate(), deviceLabel: UIDevice.current.name)
            do {
                let outcome = try await restorer.restore(status: try await client.encryption()) { progress in
                    await MainActor.run { self.message = ControllerModel.describe(progress) }
                }
                try await load(with: client)
                message = ControllerModel.summary(of: outcome)
                finished = true
            } catch let interrupted as AccessRestorer.Interrupted {
                try? await load(with: client)
                let done = interrupted.outcome.events.unlocked + interrupted.outcome.recordings.unlocked
                    + interrupted.outcome.frames.unlocked
                message = "Restoring access stopped: \(interrupted.localizedDescription) "
                    + (done > 0 ? "\(done) item\(done == 1 ? " was" : "s were") unlocked first. " : "")
                    + "Run it again to finish; nothing is done twice."
            }
        }
        return finished
    }

    private static func describe(_ progress: AccessRestorer.Progress) -> String {
        let what: String
        switch progress.stage {
        case .events: what = "older recordings"
        case .recordings: what = "older test clips"
        case .timeLapseDays: what = "older time-lapse days"
        }
        return "Unlocking \(what) for this phone: \(progress.done + 1) of \(progress.total)…"
    }

    private static func summary(of outcome: AccessRestorer.Outcome) -> String {
        func plural(_ count: Int, _ noun: String) -> String { "\(count) \(noun)\(count == 1 ? "" : "s")" }
        let opened = [(outcome.events.unlocked, "older recording"), (outcome.recordings.unlocked, "test clip"),
                      (outcome.frames.unlocked, "time-lapse frame")]
            .filter { $0.0 > 0 }.map { plural($0.0, $0.1) }
        var summary = "This phone can open recordings again. "
            + (opened.isEmpty ? "Nothing older needed unlocking" : "Unlocked " + opened.joined(separator: " and "))
        let already = outcome.events.already + outcome.recordings.already
        if already > 0 { summary += "; \(plural(already, "recording")) \(already == 1 ? "was" : "were") already open on this phone" }
        let locked = outcome.events.unreadable + outcome.recordings.unreadable
        if locked + outcome.frames.unreadable > 0 {
            summary += "; \(plural(locked, "recording")) and \(plural(outcome.frames.unreadable, "frame")) belong to an earlier recovery key and stay locked"
        }
        return summary + "."
    }

    func disableEncryption() async {
        await perform {
            _ = try await connected().disableEncryption()
            try await load(with: try connected())
            message = "Encryption is off. New recordings are stored unencrypted; encrypted ones stay encrypted."
        }
    }
}
