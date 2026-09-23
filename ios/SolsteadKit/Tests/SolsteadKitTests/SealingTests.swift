import XCTest
@testable import SolsteadKit

final class SealingTests: XCTestCase {
    // From the age specification's test keys.
    let specIdentity = "AGE-SECRET-KEY-1GFPYYSJZGFPYYSJZGFPYYSJZGFPYYSJZGFPYYSJZGFPYYSJZGFPQ4EGAEX"
    let specRecipient = "age1zvkyg2lqzraa2lnjvqej32nkuu0ues2s82hzrye869xeexvn73equnujwj"

    // Sealed by the controller's Python implementation (controller/solstead/sealing.py).
    let controllerIdentity = "AGE-SECRET-KEY-1NQYMELR9276XUJ9U94V7JMG5JD30906A0F3W4CDNZZJ3GFL0YP4SPHUJ9T"
    let controllerSealed = """
    YWdlLWVuY3J5cHRpb24ub3JnL3YxCi0+IFgyNTUxOSBnSDNyZHBuMUJLcXdSZGEwUUJHZkd0djZpbzI4dDdOTzVQVFRSOFd4K0ZBClpPSWxLY2lKdGYyV3QwNXhTNENDNEVNMitFZUlzZUR1QUVRTXZydWtDNW8KLS0tIFBEVnJORWcxcGhJQTVweFpIQVgrTTRTc3JiRkdMd2QrZXIwRkU0MHlDVmcKQr+A1ZJedd7a44VpZHsmwWFCqNrEoIpYRQZHASj5Vfiehx5oubzhu+m3J2QxrCN2ylfyrvqm9mimtp9QPO3u4zhrGAs9pYrdmgpy1m6WJjkazjul/hNGtLbjh88Bz1IoPd8gXSrXgrWmjzi1t1Q34BKg4z06glrUL99BjSkhhLnQmGfZ1AoUIYMQfCnnLf0QXOrWQiDTBUeCfXaFGzDrcJO3lShgXGgaYndGrIhGJtQw2l+vTGWcBYYf/eQ2dWjJDKiyLVJFM+YY87+FC8zTWTDaOSX9cu9j5HyqqPDq3U2Y/OV2AjE0iw==
    """

    func testSpecIdentityDerivesSpecRecipient() throws {
        let identity = try AgeIdentity(string: specIdentity)
        XCTAssertEqual(identity.recipient, specRecipient)
        XCTAssertEqual(identity.string, specIdentity)
    }

    func testOpensWhatTheControllerSealed() throws {
        let sealed = try XCTUnwrap(Data(base64Encoded: controllerSealed))
        XCTAssertTrue(Age.isSealed(sealed))
        let plain = try Age.open(sealed, with: [AgeIdentity(string: controllerIdentity)])
        XCTAssertEqual(String(decoding: plain, as: UTF8.self), String(repeating: "Solstead event clip ", count: 10))
        XCTAssertThrowsError(try Age.open(sealed, with: [AgeIdentity()])) {
            XCTAssertEqual($0 as? SealingError, .noMatchingKey)
        }
    }

    func testRoundTripAcrossChunkBoundaries() throws {
        let device = AgeIdentity(), recovery = AgeIdentity()
        for size in [0, 1, 65_535, 65_536, 65_537, 3 * 65_536 + 7] {
            let data = Age.randomBytes(size)
            let sealed = try Age.seal(data, to: [device.recipient, recovery.recipient])
            XCTAssertEqual(try Age.open(sealed, with: [device]), data, "size \(size)")
            XCTAssertEqual(try Age.open(sealed, with: [AgeIdentity(), recovery]), data, "size \(size)")
        }
    }

    func testTamperingAndTruncationAreDetected() throws {
        let identity = AgeIdentity()
        var sealed = try Age.seal(Age.randomBytes(100_000), to: [identity.recipient])
        let truncated = sealed.prefix(sealed.count - 20_000)
        XCTAssertThrowsError(try Age.open(Data(truncated), with: [identity]))
        sealed[sealed.count - 3] ^= 1
        XCTAssertThrowsError(try Age.open(sealed, with: [identity])) {
            XCTAssertEqual($0 as? SealingError, .payloadAltered)
        }
        XCTAssertThrowsError(try Age.open(Data("not a recording".utf8), with: [identity]))
    }

    func testOpenFileStreamsToDiskAndCleansUpOnFailure() throws {
        let identity = AgeIdentity()
        let data = Age.randomBytes(200_000)
        let folder = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: folder) }
        let sealed = folder.appendingPathComponent("clip.mp4.age"), plain = folder.appendingPathComponent("clip.mp4")
        try Age.seal(data, to: [identity.recipient]).write(to: sealed)
        try Age.open(fileAt: sealed, to: plain, with: [identity])
        XCTAssertEqual(try Data(contentsOf: plain), data)
        XCTAssertThrowsError(try Age.open(fileAt: sealed, to: plain, with: [AgeIdentity()]))
        XCTAssertFalse(FileManager.default.fileExists(atPath: plain.path))
    }

    func testBech32RejectsTyposAndWrongKinds() {
        let typo = String(specIdentity.dropLast()) + "Q"
        XCTAssertThrowsError(try AgeIdentity(string: typo))
        XCTAssertThrowsError(try AgeIdentity(string: specRecipient))
        XCTAssertThrowsError(try AgeIdentity.recipientKey(specIdentity))
        XCTAssertThrowsError(try AgeIdentity(string: "AGE-SECRET-KEY-1gfpy" + specIdentity.dropFirst(20)))
    }

    // MARK: Recovery key

    func testRecoveryCodeGroupsAndTypeBack() throws {
        let key = RecoveryKey(identity: try AgeIdentity(string: specIdentity))
        XCTAssertEqual(key.groups.joined(), String(specIdentity.dropFirst(RecoveryKey.prefix.count)))
        XCTAssertEqual(key.groups.count, 12)
        XCTAssertTrue(key.groups.dropLast().allSatisfy { $0.count == 5 })
        XCTAssertTrue(key.matches(group: 0, typed: " gfpyy "))
        XCTAssertFalse(key.matches(group: 0, typed: "GFPYZ"))
        XCTAssertFalse(key.matches(group: 99, typed: "GFPYY"))
        let challenge = RecoveryKey.challenge(groupCount: key.groups.count)
        XCTAssertEqual(challenge.count, 2)
        XCTAssertNotEqual(challenge[0], challenge[1])
    }

    func testRecoveryKeyParsesTypedCodeAndFile() throws {
        let key = RecoveryKey()
        let spaced = key.groups.joined(separator: " ").lowercased()
        XCTAssertEqual(try RecoveryKey.parse(spaced).recipient, key.recipient)
        XCTAssertEqual(try RecoveryKey.parse(key.code).recipient, key.recipient)
        let file = key.fileContents(controllerName: "Barn")
        XCTAssertTrue(file.contains("# public key: \(key.recipient)\n\(key.code)\n"))
        XCTAssertEqual(try RecoveryKey.parse(file).recipient, key.recipient)
        XCTAssertThrowsError(try RecoveryKey.parse("GFPYY GFPYY"))
    }

    // MARK: Client

    func testEnrolDisableAndDecryptingClipDownload() async throws {
        let transport = StubTransport()
        let pairing = try Pairing(url: "https://solstead.local:8443", token: String(repeating: "k", count: 32),
                                  fingerprint: String(repeating: "cd", count: 32))
        let client = ControllerClient(pairing: pairing, transport: transport)
        let device = AgeIdentity(), recovery = RecoveryKey()
        let status = Data("""
        {"enrolled": true, "format": "age-encryption.org/v1", "unencrypted_events": 0, "recipients": [
          {"role": "device", "label": "iPhone", "public_key": "\(device.recipient)", "added_unix": 5},
          {"role": "recovery", "label": "Recovery key", "public_key": "\(recovery.recipient)", "added_unix": 5}]}
        """.utf8)
        transport.responses = [(200, status)]
        let enrolled = try await client.enroll(Enrolment(devices: [("iPhone", device.recipient)],
                                                         recoveryPublicKey: recovery.recipient))
        XCTAssertEqual(enrolled.deviceKeys.map(\.publicKey), [device.recipient])
        let sent = try XCTUnwrap(transport.requests.last)
        XCTAssertEqual(sent.httpMethod, "PUT")
        let body = try XCTUnwrap(JSONSerialization.jsonObject(with: XCTUnwrap(sent.httpBody)) as? [String: [[String: String]]])
        XCTAssertEqual(body["recipients"]?.map { $0["role"] }, ["device", "recovery"])
        XCTAssertEqual(body["recipients"]?.last?["public_key"], recovery.recipient)

        transport.responses = [(200, Data(#"{"enrolled": false, "format": "age-encryption.org/v1", "recipients": []}"#.utf8))]
        let off = try await client.disableEncryption()
        XCTAssertFalse(off.enrolled)
        XCTAssertEqual(transport.requests.last?.httpMethod, "DELETE")

        let clip = Data("mp4 bytes".utf8)
        let event = try ControllerJSON.decoder.decode(Event.self, from: Data("""
        {"id": "e1", "started_unix": 1, "ended_unix": 2, "clip_from_unix": 0, "clip_to_unix": 3,
         "size_bytes": 9, "trigger": "motion", "created_unix": 3, "duration_seconds": 3, "encrypted": true}
        """.utf8))
        transport.responses = [(200, try Age.seal(clip, to: [device.recipient, recovery.recipient]))]
        let url = try await client.downloadEventClip(event, identities: [device])
        XCTAssertEqual(url.lastPathComponent, "e1.mp4")
        XCTAssertEqual(try Data(contentsOf: url), clip)
        XCTAssertFalse(FileManager.default.fileExists(atPath: url.path + ".age"))

        transport.responses = [(200, try Age.seal(clip, to: [recovery.recipient]))]
        do {
            _ = try await client.downloadEventClip(event, identities: [device])
            XCTFail("a phone without a matching key must not play the clip")
        } catch {
            XCTAssertEqual(error as? SealingError, .noMatchingKey)
        }
    }

    // MARK: Restoring access

    func header(of sealed: Data) -> Data {
        let end = sealed.range(of: Data("\n---".utf8))!.upperBound
        let newline = sealed[end...].firstIndex(of: 0x0A)!
        return sealed[..<(newline + 1)]
    }

    func testRewrapLetsANewPhoneOpenAnOlderClipAndKeepsEveryoneElse() throws {
        let oldPhone = AgeIdentity(), recovery = AgeIdentity(), newPhone = AgeIdentity()
        let data = Age.randomBytes(150_000)
        let sealed = try Age.seal(data, to: [oldPhone.recipient, recovery.recipient])
        let old = header(of: sealed)
        XCTAssertFalse(Age.canOpen(header: old, with: [newPhone]))
        XCTAssertTrue(Age.canOpen(header: old, with: [recovery]))

        let rewrapped = try Age.rewrap(header: old, using: recovery, adding: newPhone.recipient)
        XCTAssertTrue(rewrapped.starts(with: old.prefix(old.count - 50)), "existing stanzas are kept first")
        let updated = rewrapped + sealed.dropFirst(old.count)
        for identity in [newPhone, oldPhone, recovery] {
            XCTAssertEqual(try Age.open(updated, with: [identity]), data)
        }
        XCTAssertTrue(Age.canOpen(header: rewrapped, with: [newPhone]))
    }

    func testRewrapOfAControllerSealedClip() throws {
        let sealed = try XCTUnwrap(Data(base64Encoded: controllerSealed))
        let recovery = try AgeIdentity(string: controllerIdentity), newPhone = AgeIdentity()
        let old = header(of: sealed)
        let updated = try Age.rewrap(header: old, using: recovery, adding: newPhone.recipient) + sealed.dropFirst(old.count)
        XCTAssertEqual(String(decoding: try Age.open(updated, with: [newPhone]), as: UTF8.self),
                       String(repeating: "Solstead event clip ", count: 10))
    }

    func testRewrapRefusesAWrongKeyOrAlteredHeader() throws {
        let owner = AgeIdentity()
        var old = header(of: try Age.seal(Data("x".utf8), to: [owner.recipient]))
        XCTAssertThrowsError(try Age.rewrap(header: old, using: AgeIdentity(), adding: owner.recipient)) {
            XCTAssertEqual($0 as? SealingError, .noMatchingKey)
        }
        old[old.count - 5] = old[old.count - 5] == UInt8(ascii: "A") ? UInt8(ascii: "B") : UInt8(ascii: "A")
        XCTAssertThrowsError(try Age.rewrap(header: old, using: owner, adding: AgeIdentity().recipient))
    }

    func testClientHeaderCallsAndEventLimit() async throws {
        let transport = StubTransport()
        let pairing = try Pairing(url: "https://solstead.local:8443", token: String(repeating: "k", count: 32),
                                  fingerprint: String(repeating: "cd", count: 32))
        let client = ControllerClient(pairing: pairing, transport: transport)
        let eventJSON = Data("""
        {"id": "e1", "started_unix": 1, "ended_unix": 2, "clip_from_unix": 0, "clip_to_unix": 3,
         "size_bytes": 900, "trigger": "motion", "created_unix": 3, "duration_seconds": 3, "encrypted": true}
        """.utf8)
        let event = try ControllerJSON.decoder.decode(Event.self, from: eventJSON)
        transport.responses = [(200, Data(#"{"events": []}"#.utf8)), (200, Data("age-encryption.org/v1\n".utf8)), (200, eventJSON),
                               (200, Data(#"{"deleted": "e1"}"#.utf8))]
        _ = try await client.events(limit: 100000)
        XCTAssertEqual(transport.requests[0].url?.absoluteString, "https://solstead.local:8443/v1/events?limit=100000")
        _ = try await client.eventHeader(event)
        XCTAssertEqual(transport.requests[1].url?.path, "/v1/events/e1/header")
        try await client.replaceEventHeader(event, with: Data("header".utf8))
        try await client.deleteEvent(event)
        XCTAssertEqual(transport.requests[3].httpMethod, "DELETE")
        XCTAssertEqual(transport.requests[3].url?.path, "/v1/events/e1")
        XCTAssertEqual(transport.requests[2].httpMethod, "PUT")
        XCTAssertEqual(transport.requests[2].value(forHTTPHeaderField: "Content-Type"), "application/octet-stream")
        XCTAssertEqual(transport.requests[2].httpBody, Data("header".utf8))
    }
}
