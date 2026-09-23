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
            let others = (encryption?.deviceKeys ?? []).filter { $0.publicKey != device.recipient }
            let enrolment = Enrolment(
                devices: others.map { ($0.label, $0.publicKey) } + [(UIDevice.current.name, device.recipient)],
                recoveryPublicKey: recovery.recipient)
            _ = try await connected().enroll(enrolment)
            try await load(with: try connected())
            recoverySetup = nil
            message = "Encryption is on. New recordings can only be opened with this phone or your recovery key."
        }
    }

    /// The public half of the recovery key new recordings are sealed to.
    var recoveryPublicKey: String? {
        encryption?.recipients.first { $0.role == "recovery" }?.publicKey
    }

    /// Restores access on this phone with the owner's recovery key: enrols this phone's key and
    /// adds it to every older sealed clip's header, one small download and upload per clip. The
    /// recovery key is used here and not kept. Returns true when finished.
    func restoreAccess(with recovery: AgeIdentity) async -> Bool {
        var finished = false
        await perform {
            guard let expected = recoveryPublicKey else {
                throw ControllerError.controller("Encryption is not on for this controller.", status: 0)
            }
            guard recovery.recipient == expected else {
                throw ControllerError.controller(
                    "That recovery key is not the one this controller uses. Check it is the key from the most recent setup.",
                    status: 0)
            }
            let client = try connected()
            let device = try DeviceKeyStore.loadOrCreate()
            if !thisPhoneEnrolled {
                let others = (encryption?.deviceKeys ?? []).filter { $0.publicKey != device.recipient }
                _ = try await client.enroll(Enrolment(
                    devices: others.map { ($0.label, $0.publicKey) } + [(UIDevice.current.name, device.recipient)],
                    recoveryPublicKey: expected))
            }
            let sealed = try await client.events(limit: 100_000).filter(\.isEncrypted)
            var unlocked = 0, already = 0, unreadable = 0
            for (index, event) in sealed.enumerated() {
                message = "Unlocking older recordings for this phone: \(index + 1) of \(sealed.count)…"
                do {
                    let header = try await client.eventHeader(event)
                    if Age.canOpen(header: header, with: [device]) {
                        already += 1
                        continue
                    }
                    try await client.replaceEventHeader(event, with: Age.rewrap(header: header, using: recovery, adding: device.recipient))
                    unlocked += 1
                } catch SealingError.noMatchingKey {
                    unreadable += 1  // sealed under an earlier setup with a different recovery key
                } catch let ControllerError.controller(_, status) where status == 404 {
                    continue  // overwritten while we were working
                }
            }
            try await load(with: client)
            var summary = "This phone can open recordings again. Unlocked \(unlocked) older recording\(unlocked == 1 ? "" : "s")"
            if already > 0 { summary += "; \(already) already opened on this phone" }
            if unreadable > 0 { summary += "; \(unreadable) belong to an earlier recovery key and stay locked" }
            message = summary + "."
            finished = true
        }
        return finished
    }

    func disableEncryption() async {
        await perform {
            _ = try await connected().disableEncryption()
            try await load(with: try connected())
            message = "Encryption is off. New recordings are stored unencrypted; encrypted ones stay encrypted."
        }
    }
}
