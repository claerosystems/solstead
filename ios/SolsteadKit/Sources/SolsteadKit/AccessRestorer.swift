import Foundation

/// Restoring access (ADR 0003): a phone holding the owner's recovery key enrols its own device
/// key, then adds it to every recording sealed before it was enrolled: event clips, manual clips
/// and time-lapse frames. Only headers travel; the controller never sees a private key and the
/// recovery key is not kept.
///
/// Running it again is always safe: recordings this phone already opens are counted, not changed.
public struct AccessRestorer: Sendable {
    public enum Failure: LocalizedError, Equatable {
        case notEnabled
        case wrongRecoveryKey

        public var errorDescription: String? {
            switch self {
            case .notEnabled: return "Encryption is not on for this controller."
            case .wrongRecoveryKey:
                return "That recovery key is not the one this controller uses. Check it is the key from the most recent setup."
            }
        }
    }

    /// What was done, by kind of recording.
    public struct Outcome: Equatable, Sendable {
        /// This phone's key was added to the ones new recordings are sealed to.
        public var enrolled = false
        public var events = ControllerClient.RewrapCount()
        public var recordings = ControllerClient.RewrapCount()
        public var frames = ControllerClient.RewrapCount()
        public init() {}
    }

    /// Stopped partway, by a dropped connection or a controller error. Everything counted in
    /// `outcome` is done; running the restore again picks up the rest.
    public struct Interrupted: LocalizedError {
        public let outcome: Outcome
        public let underlying: Error
        public var errorDescription: String? { underlying.localizedDescription }
    }

    public enum Stage: Equatable, Sendable {
        case events, recordings, timeLapseDays
    }

    /// About to work on item `done + 1` of `total` in `stage`.
    public struct Progress: Equatable, Sendable {
        public let stage: Stage
        public let done: Int
        public let total: Int
    }

    let client: ControllerClient
    let recovery: AgeIdentity
    let device: AgeIdentity
    let deviceLabel: String

    public init(client: ControllerClient, recovery: AgeIdentity, device: AgeIdentity, deviceLabel: String) {
        self.client = client
        self.recovery = recovery
        self.device = device
        self.deviceLabel = deviceLabel
    }

    /// Checks the recovery key against `status` (the controller's current encryption status),
    /// enrols this phone if it is not enrolled yet, then re-wraps event clips, manual clips and
    /// time-lapse days, in that order. Throws `Failure` before touching anything, or
    /// `Interrupted` with what was done so far.
    public func restore(status: EncryptionStatus,
                        progress: @escaping @Sendable (Progress) async -> Void = { _ in }) async throws -> Outcome {
        guard status.enrolled, let recoveryKey = status.recoveryKey else { throw Failure.notEnabled }
        guard recovery.recipient == recoveryKey.publicKey else { throw Failure.wrongRecoveryKey }
        var outcome = Outcome()
        do {
            if !status.deviceKeys.contains(where: { $0.publicKey == device.recipient }) {
                _ = try await client.enroll(status.enrolment(adding: (deviceLabel, device.recipient),
                                                             recovery: recoveryKey.publicKey))
                outcome.enrolled = true
            }

            let events = try await client.events(limit: ControllerClient.listingLimit).filter(\.isEncrypted)
            for (index, event) in events.enumerated() {
                await progress(Progress(stage: .events, done: index, total: events.count))
                try await rewrap(&outcome.events, header: { try await client.eventHeader(event) },
                                 replace: { _ = try await client.replaceEventHeader(event, with: $0) })
            }

            let recordings = try await client.recordings(limit: ControllerClient.listingLimit).filter(\.isEncrypted)
            for (index, recording) in recordings.enumerated() {
                await progress(Progress(stage: .recordings, done: index, total: recordings.count))
                try await rewrap(&outcome.recordings, header: { try await client.recordingHeader(recording) },
                                 replace: { _ = try await client.replaceRecordingHeader(recording, with: $0) })
            }

            // Frames: one header download and a few batched uploads per day.
            let days = try await client.timelapse().days?.filter(\.isEncrypted) ?? []
            for (index, day) in days.enumerated() {
                await progress(Progress(stage: .timeLapseDays, done: index, total: days.count))
                do {
                    let count = try await client.rewrapTimelapseDay(day.date, using: recovery, adding: device)
                    outcome.frames.unlocked += count.unlocked
                    outcome.frames.already += count.already
                    outcome.frames.unreadable += count.unreadable
                } catch let ControllerError.controller(_, code) where code == 404 {
                    continue  // the whole day was overwritten meanwhile
                }
            }
        } catch {
            throw Interrupted(outcome: outcome, underlying: error)
        }
        return outcome
    }

    /// Adds this phone to one sealed recording's header, counting what happened.
    private func rewrap(_ count: inout ControllerClient.RewrapCount,
                        header fetch: () async throws -> Data,
                        replace: (Data) async throws -> Void) async throws {
        let header: Data
        do {
            header = try await fetch()
        } catch let ControllerError.controller(_, status) where status == 404 {
            return  // overwritten or deleted meanwhile
        }
        if Age.canOpen(header: header, with: [device]) {
            count.already += 1
            return
        }
        let rewrapped: Data
        do {
            rewrapped = try Age.rewrap(header: header, using: recovery, adding: device.recipient)
        } catch SealingError.noMatchingKey {
            count.unreadable += 1  // sealed under an earlier setup with a different recovery key
            return
        }
        do {
            try await replace(rewrapped)
        } catch let ControllerError.controller(_, status) where status == 404 {
            return
        }
        count.unlocked += 1
    }
}
