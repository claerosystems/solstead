import XCTest
@testable import SolsteadKit

/// Restoring access with the recovery key, against a routed fake controller and real age keys.
final class AccessRestorerTests: XCTestCase {
    let day = "2026-09-22"
    var transport = RoutingTransport()
    var client: ControllerClient!
    let oldPhone = AgeIdentity(), recovery = AgeIdentity(), newPhone = AgeIdentity()

    override func setUpWithError() throws {
        transport = RoutingTransport()
        let pairing = try Pairing(url: "https://solstead.local:8443", token: String(repeating: "k", count: 32),
                                  fingerprint: String(repeating: "cd", count: 32))
        client = ControllerClient(pairing: pairing, transport: transport)
    }

    func status(devices: [(String, AgeIdentity)], recovery: AgeIdentity?) throws -> EncryptionStatus {
        var keys = devices.map { #"{"role": "device", "label": "\#($0.0)", "public_key": "\#($0.1.recipient)", "added_unix": 5}"# }
        if let recovery {
            keys.append(#"{"role": "recovery", "label": "Recovery key", "public_key": "\#(recovery.recipient)", "added_unix": 5}"#)
        }
        let json = #"{"enrolled": \#(recovery != nil), "format": "age-encryption.org/v1", "recipients": [\#(keys.joined(separator: ","))]}"#
        return try ControllerJSON.decoder.decode(EncryptionStatus.self, from: Data(json.utf8))
    }

    /// Just the age header of a file sealed to `recipients`.
    func header(_ recipients: [AgeIdentity]) throws -> Data {
        let sealed = try Age.seal(Data("footage".utf8), to: recipients.map(\.recipient))
        let end = sealed.range(of: Data("\n--- ".utf8))!.upperBound
        return sealed[..<(sealed[end...].firstIndex(of: 0x0A)! + 1)]
    }

    func event(_ id: String, encrypted: Bool = true) -> String {
        #"{"id": "\#(id)", "started_unix": 1, "ended_unix": 2, "clip_from_unix": 0, "clip_to_unix": 3, "size_bytes": 10, "trigger": "motion", "created_unix": 3, "duration_seconds": 3, "encrypted": \#(encrypted)}"#
    }

    func recording(_ id: String) -> String {
        #"{"id": "\#(id)", "size_bytes": 10, "created_unix": 3, "encrypted": true}"#
    }

    func route(_ key: String, _ body: String, status: Int = 200) {
        transport.routes[key] = (status, Data(body.utf8))
    }

    func routeHeader(_ key: String, sealedTo recipients: [AgeIdentity]) throws {
        let sealed = try header(recipients)
        transport.routes[key] = (200, sealed)
    }

    func puts(_ path: String) -> [Data] {
        transport.requests.filter { $0.httpMethod == "PUT" && $0.url?.path == path }.compactMap(\.httpBody)
    }

    // MARK: Refusals

    func testAWrongRecoveryKeyIsRefusedBeforeAnyRequest() async throws {
        let restorer = AccessRestorer(client: client, recovery: AgeIdentity(), device: newPhone, deviceLabel: "New")
        do {
            _ = try await restorer.restore(status: status(devices: [("Old", oldPhone)], recovery: recovery))
            XCTFail("expected a refusal")
        } catch let failure as AccessRestorer.Failure {
            XCTAssertEqual(failure, .wrongRecoveryKey)
        }
        do {
            _ = try await restorer.restore(status: status(devices: [], recovery: nil))
            XCTFail("expected a refusal")
        } catch let failure as AccessRestorer.Failure {
            XCTAssertEqual(failure, .notEnabled)
        }
        XCTAssertTrue(transport.requests.isEmpty)
    }

    // MARK: Restoring

    func testNewPhoneIsEnrolledAndEveryKindOfOlderRecordingIsUnlocked() async throws {
        let earlierRecovery = AgeIdentity()
        route("PUT /v1/encryption", #"{"enrolled": true, "format": "age-encryption.org/v1", "recipients": []}"#)
        route("GET /v1/events", #"{"events": [\#(event("e1")), \#(event("e2")), \#(event("e3")), \#(event("plain", encrypted: false)), \#(event("gone"))]}"#)
        try routeHeader("GET /v1/events/e1/header", sealedTo: [oldPhone, recovery])
        try routeHeader("GET /v1/events/e2/header", sealedTo: [newPhone, recovery])  // already opens
        try routeHeader("GET /v1/events/e3/header", sealedTo: [oldPhone, earlierRecovery])
        route("PUT /v1/events/e1/header", event("e1"))
        // "gone" answers 404: pruned while the restore was running.
        route("GET /v1/recordings", #"{"recordings": [\#(recording("m1.mp4.age"))]}"#)
        try routeHeader("GET /v1/recordings/m1.mp4.age/header", sealedTo: [oldPhone, recovery])
        route("PUT /v1/recordings/m1.mp4.age/header", recording("m1.mp4.age"))
        route("GET /v1/timelapse", #"{"enabled": true, "interval_seconds": 300, "window": "06:00-18:00", "width": 1920, "allocation_bytes": 1, "export_fps": 12, "day_count": 1, "frames": 1, "used_bytes": 1, "frames_per_day": 144, "estimated_retention_days": null, "oldest_date": "\#(day)", "newest_unix": 1, "last_error": null, "days": [{"date": "\#(day)", "frames": 1, "bytes": 1, "encrypted_frames": 1}]}"#)
        let frame = try header([oldPhone, recovery]).base64EncodedString()
        route("GET /v1/timelapse/\(day)/headers", #"{"date": "\#(day)", "headers": {"090000.jpg.age": "\#(frame)"}}"#)
        route("PUT /v1/timelapse/\(day)/headers", #"{"date": "\#(day)", "replaced": 1}"#)

        let stages = StageLog()
        let restorer = AccessRestorer(client: client, recovery: recovery, device: newPhone, deviceLabel: "New iPhone")
        let outcome = try await restorer.restore(status: status(devices: [("Old iPhone", oldPhone)], recovery: recovery)) {
            await stages.add($0)
        }

        XCTAssertTrue(outcome.enrolled)
        XCTAssertEqual(outcome.events, count(unlocked: 1, already: 1, unreadable: 1))
        XCTAssertEqual(outcome.recordings, count(unlocked: 1))
        XCTAssertEqual(outcome.frames, count(unlocked: 1))

        // The enrolment keeps the old phone, adds this one, and keeps the recovery key.
        let enrolment = try XCTUnwrap(puts("/v1/encryption").first)
        let sent = try XCTUnwrap(JSONSerialization.jsonObject(with: enrolment) as? [String: [[String: String]]])
        XCTAssertEqual(sent["recipients"]?.map { $0["public_key"] }, [oldPhone.recipient, newPhone.recipient, recovery.recipient])
        XCTAssertEqual(sent["recipients"]?.map { $0["label"] }, ["Old iPhone", "New iPhone", "Recovery key"])

        // Every re-wrapped header still opens for every key it had, and now for this phone too.
        for path in ["/v1/events/e1/header", "/v1/recordings/m1.mp4.age/header"] {
            let rewrapped = try XCTUnwrap(puts(path).first, path)
            for key in [newPhone, oldPhone, recovery] {
                XCTAssertTrue(Age.canOpen(header: rewrapped, with: [key]), path)
            }
        }
        XCTAssertTrue(puts("/v1/events/e2/header").isEmpty && puts("/v1/events/e3/header").isEmpty)
        XCTAssertTrue(transport.requests.contains { $0.url?.query == "limit=\(ControllerClient.listingLimit)" && $0.url?.path == "/v1/recordings" })

        let seen = await stages.entries
        XCTAssertEqual(seen.first, AccessRestorer.Progress(stage: .events, done: 0, total: 4))
        XCTAssertEqual(seen.map(\.stage), [.events, .events, .events, .events, .recordings, .timeLapseDays])
    }

    func testAnAlreadyEnrolledPhoneIsNotEnrolledAgain() async throws {
        route("GET /v1/events", #"{"events": []}"#)
        route("GET /v1/recordings", #"{"recordings": []}"#)
        route("GET /v1/timelapse", #"{"enabled": false, "interval_seconds": 300, "window": "06:00-18:00", "width": 1920, "allocation_bytes": 1, "export_fps": 12, "day_count": 0, "frames": 0, "used_bytes": 0, "frames_per_day": 144, "estimated_retention_days": null, "oldest_date": null, "newest_unix": null, "last_error": null, "days": []}"#)
        let restorer = AccessRestorer(client: client, recovery: recovery, device: newPhone, deviceLabel: "New")
        let outcome = try await restorer.restore(status: status(devices: [("New", newPhone)], recovery: recovery))
        XCTAssertEqual(outcome, AccessRestorer.Outcome())
        XCTAssertTrue(puts("/v1/encryption").isEmpty)
    }

    func testAControllerErrorStopsTheRestoreAndKeepsWhatWasDone() async throws {
        route("PUT /v1/encryption", #"{"enrolled": true, "format": "age-encryption.org/v1", "recipients": []}"#)
        route("GET /v1/events", #"{"events": [\#(event("e1")), \#(event("e2"))]}"#)
        try routeHeader("GET /v1/events/e1/header", sealedTo: [oldPhone, recovery])
        route("PUT /v1/events/e1/header", event("e1"))
        route("GET /v1/events/e2/header", #"{"error": "Recording SSD is not mounted"}"#, status: 503)

        let restorer = AccessRestorer(client: client, recovery: recovery, device: newPhone, deviceLabel: "New")
        do {
            _ = try await restorer.restore(status: status(devices: [("Old", oldPhone)], recovery: recovery))
            XCTFail("expected the restore to stop")
        } catch let interrupted as AccessRestorer.Interrupted {
            XCTAssertTrue(interrupted.outcome.enrolled)
            XCTAssertEqual(interrupted.outcome.events, count(unlocked: 1))
            XCTAssertEqual(interrupted.localizedDescription, "Recording SSD is not mounted")
        }
        XCTAssertFalse(transport.requests.contains { $0.url?.path == "/v1/recordings" })  // stopped there
    }

    // MARK: Enrolment

    func testEnrolmentKeepsOtherPhonesOnceAndSetsTheRecoveryKey() throws {
        let current = try status(devices: [("Old", oldPhone), ("New", newPhone)], recovery: recovery)
        XCTAssertEqual(current.recoveryKey?.publicKey, recovery.recipient)
        let replacement = AgeIdentity()
        let enrolment = current.enrolment(adding: ("New again", newPhone.recipient), recovery: replacement.recipient)
        XCTAssertEqual(enrolment.recipients.map(\.publicKey), [oldPhone.recipient, newPhone.recipient, replacement.recipient])
        XCTAssertEqual(enrolment.recipients.map(\.role), ["device", "device", "recovery"])
        XCTAssertEqual(enrolment.recipients[1].label, "New again")
        XCTAssertNil(try status(devices: [], recovery: nil).recoveryKey)
    }

    func count(unlocked: Int = 0, already: Int = 0, unreadable: Int = 0) -> ControllerClient.RewrapCount {
        var count = ControllerClient.RewrapCount()
        count.unlocked = unlocked
        count.already = already
        count.unreadable = unreadable
        return count
    }
}

actor StageLog {
    private(set) var entries: [AccessRestorer.Progress] = []
    func add(_ progress: AccessRestorer.Progress) { entries.append(progress) }
}
