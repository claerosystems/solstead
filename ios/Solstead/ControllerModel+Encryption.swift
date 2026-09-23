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

    func disableEncryption() async {
        await perform {
            _ = try await connected().disableEncryption()
            try await load(with: try connected())
            message = "Encryption is off. New recordings are stored unencrypted; encrypted ones stay encrypted."
        }
    }
}
