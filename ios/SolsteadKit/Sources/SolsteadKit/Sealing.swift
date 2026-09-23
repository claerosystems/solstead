import CryptoKit
import Foundation

/// Opens recordings the controller sealed in the age v1 file format (age-encryption.org/v1) and
/// holds the X25519 keys that can open them. See ADR 0003: the controller only ever has the
/// public half ("recipient"); the phone's device key and the owner's recovery key decrypt.
public enum SealingError: LocalizedError, Equatable {
    case malformedKey(String)
    case notSealed
    case malformedHeader
    case noMatchingKey
    case headerAltered
    case payloadAltered

    public var errorDescription: String? {
        switch self {
        case .malformedKey(let reason): return reason
        case .notSealed: return "This file is not an encrypted Solstead recording."
        case .malformedHeader: return "The encrypted recording is damaged."
        case .noMatchingKey: return "This phone can't open this recording yet. Use Restore access in Settings with your recovery key."
        case .headerAltered, .payloadAltered: return "The encrypted recording is damaged or has been altered."
        }
    }
}

// MARK: - Keys

/// An age X25519 key pair. `string` is the secret (`AGE-SECRET-KEY-1…`); `recipient` is the
/// public key (`age1…`) sent to the controller.
public struct AgeIdentity: Sendable {
    static let identityHRP = "age-secret-key-"
    static let recipientHRP = "age"

    let privateKey: Curve25519.KeyAgreement.PrivateKey

    public init() { privateKey = Curve25519.KeyAgreement.PrivateKey() }

    public init(rawRepresentation: Data) throws {
        guard rawRepresentation.count == 32,
              let key = try? Curve25519.KeyAgreement.PrivateKey(rawRepresentation: rawRepresentation)
        else { throw SealingError.malformedKey("A key must be 32 bytes.") }
        privateKey = key
    }

    public init(string: String) throws {
        let (hrp, data) = try Bech32.decode(string.trimmingCharacters(in: .whitespacesAndNewlines))
        guard hrp == Self.identityHRP else {
            throw SealingError.malformedKey("That is not a recovery key (AGE-SECRET-KEY-1…).")
        }
        try self.init(rawRepresentation: data)
    }

    public var rawRepresentation: Data { privateKey.rawRepresentation }
    public var string: String { Bech32.encode(hrp: Self.identityHRP, data: rawRepresentation).uppercased() }
    public var recipient: String { Bech32.encode(hrp: Self.recipientHRP, data: privateKey.publicKey.rawRepresentation) }

    /// The 32-byte public key from an `age1…` string.
    public static func recipientKey(_ recipient: String) throws -> Data {
        let (hrp, data) = try Bech32.decode(recipient.trimmingCharacters(in: .whitespacesAndNewlines))
        guard hrp == recipientHRP, data.count == 32 else {
            throw SealingError.malformedKey("That is not an age public key (age1…).")
        }
        return data
    }
}

/// Bech32 (BIP 173) as age uses it for keys, without the 90-character limit.
enum Bech32 {
    static let charset = Array("qpzry9x8gf2tvdw0s3jn54khce6mua7l")

    static func polymod(_ values: [UInt8]) -> UInt32 {
        let generator: [UInt32] = [0x3B6A57B2, 0x26508E6D, 0x1EA119FA, 0x3D4233DD, 0x2A1462B3]
        var check: UInt32 = 1
        for value in values {
            let top = check >> 25
            check = (check & 0x1FFFFFF) << 5 ^ UInt32(value)
            for i in 0..<5 where (top >> i) & 1 == 1 { check ^= generator[i] }
        }
        return check
    }

    static func expand(_ hrp: String) -> [UInt8] {
        let bytes = Array(hrp.utf8)
        return bytes.map { $0 >> 5 } + [0] + bytes.map { $0 & 31 }
    }

    static func convert(_ data: [UInt8], from source: Int, to target: Int, pad: Bool) throws -> [UInt8] {
        var acc = 0, bits = 0
        var out: [UInt8] = []
        let mask = (1 << target) - 1
        for value in data {
            acc = (acc << source) | Int(value)
            bits += source
            while bits >= target {
                bits -= target
                out.append(UInt8((acc >> bits) & mask))
            }
            acc &= (1 << bits) - 1
        }
        if pad {
            if bits > 0 { out.append(UInt8((acc << (target - bits)) & mask)) }
        } else if bits >= source || (acc << (target - bits)) & mask != 0 {
            throw SealingError.malformedKey("The key has invalid padding.")
        }
        return out
    }

    static func encode(hrp: String, data: Data) -> String {
        var words = (try? convert(Array(data), from: 8, to: 5, pad: true)) ?? []
        let check = polymod(expand(hrp) + words + [0, 0, 0, 0, 0, 0]) ^ 1
        words += (0..<6).map { UInt8((check >> (5 * (5 - UInt32($0)))) & 31) }
        return hrp + "1" + String(words.map { charset[Int($0)] })
    }

    static func decode(_ text: String) throws -> (hrp: String, data: Data) {
        guard text.lowercased() == text || text.uppercased() == text else {
            throw SealingError.malformedKey("The key mixes upper and lower case.")
        }
        let lower = text.lowercased()
        guard let split = lower.lastIndex(of: "1"), split != lower.startIndex,
              lower.distance(from: split, to: lower.endIndex) >= 7
        else { throw SealingError.malformedKey("That does not look like a key.") }
        let hrp = String(lower[..<split])
        var words: [UInt8] = []
        for character in lower[lower.index(after: split)...] {
            guard let index = charset.firstIndex(of: character) else {
                throw SealingError.malformedKey("The key contains “\(character)”, which is never used in keys.")
            }
            words.append(UInt8(index))
        }
        guard polymod(expand(hrp) + words) == 1 else {
            throw SealingError.malformedKey("The key's checksum does not match. Check it was copied correctly.")
        }
        return (hrp, Data(try convert(Array(words.dropLast(6)), from: 5, to: 8, pad: false)))
    }
}

// MARK: - The age format

public enum Age {
    static let intro = Data("age-encryption.org/v1\n".utf8)
    static let x25519Info = Data("age-encryption.org/v1/X25519".utf8)
    static let chunkSize = 64 * 1024
    static let tagSize = 16

    /// True when data starts like an age v1 file.
    public static func isSealed(_ prefix: Data) -> Bool { prefix.starts(with: intro) }

    /// Decrypts `source` into `destination` (replaced if present), a chunk at a time.
    public static func open(fileAt source: URL, to destination: URL, with identities: [AgeIdentity]) throws {
        let input = try FileHandle(forReadingFrom: source)
        defer { try? input.close() }
        FileManager.default.createFile(atPath: destination.path, contents: nil)
        let output = try FileHandle(forWritingTo: destination)
        do {
            try open(read: { try input.read(upToCount: $0) ?? Data() }, write: { try output.write(contentsOf: $0) },
                     with: identities)
            try output.close()
        } catch {
            try? output.close()
            try? FileManager.default.removeItem(at: destination)
            throw error
        }
    }

    public static func open(_ sealed: Data, with identities: [AgeIdentity]) throws -> Data {
        var offset = sealed.startIndex
        var out = Data()
        try open(read: { count in
            let end = sealed.index(offset, offsetBy: count, limitedBy: sealed.endIndex) ?? sealed.endIndex
            defer { offset = end }
            return sealed[offset..<end]
        }, write: { out.append($0) }, with: identities)
        return out
    }

    /// Seals data to `age1…` recipients. The controller does this for real recordings; the app
    /// uses it in tests.
    public static func seal(_ plaintext: Data, to recipients: [String]) throws -> Data {
        guard !recipients.isEmpty else { throw SealingError.malformedKey("No recipients.") }
        let fileKey = randomBytes(16)
        var header = intro
        for recipient in recipients {
            let theirs = try Curve25519.KeyAgreement.PublicKey(rawRepresentation: try AgeIdentity.recipientKey(recipient))
            let ephemeral = Curve25519.KeyAgreement.PrivateKey()
            let share = ephemeral.publicKey.rawRepresentation
            let shared = try ephemeral.sharedSecretFromKeyAgreement(with: theirs).withUnsafeBytes { Data($0) }
            let key = hkdf(shared, salt: share + theirs.rawRepresentation, info: x25519Info)
            let body = try aead(key, nonce: Data(count: 12), seal: fileKey)
            header += Data("-> X25519 \(b64(share))\n\(b64(body))\n".utf8)
        }
        header += Data("---".utf8)
        let mac = Data(HMAC<SHA256>.authenticationCode(for: header, using: hkdf(fileKey, salt: Data(), info: Data("header".utf8))))
        var out = header + Data(" \(b64(mac))\n".utf8)
        let nonce = randomBytes(16)
        out += nonce
        let payloadKey = hkdf(fileKey, salt: nonce, info: Data("payload".utf8))
        var counter: UInt64 = 0
        var start = plaintext.startIndex
        repeat {
            let end = plaintext.index(start, offsetBy: chunkSize, limitedBy: plaintext.endIndex) ?? plaintext.endIndex
            let last = end == plaintext.endIndex
            out += try aead(payloadKey, nonce: chunkNonce(counter, last: last), seal: plaintext[start..<end])
            start = end
            counter += 1
            if last { break }
        } while true
        return out
    }

    // MARK: Re-wrapping (restoring access on a new phone)

    /// Whether any of `identities` opens a file with this header.
    public static func canOpen(header: Data, with identities: [AgeIdentity]) -> Bool {
        (try? fileKey(header: header, identities: identities)) != nil
    }

    /// A copy of `header` that also lets `recipient` open the file, made with a key that already
    /// can (the recovery key). Every existing stanza is kept, so nobody loses access.
    public static func rewrap(header: Data, using identity: AgeIdentity, adding recipient: String) throws -> Data {
        let (stanzas, key) = try fileKey(header: header, identities: [identity])
        let theirs = try Curve25519.KeyAgreement.PublicKey(rawRepresentation: try AgeIdentity.recipientKey(recipient))
        let ephemeral = Curve25519.KeyAgreement.PrivateKey()
        let share = ephemeral.publicKey.rawRepresentation
        let shared = try ephemeral.sharedSecretFromKeyAgreement(with: theirs).withUnsafeBytes { Data($0) }
        let wrapKey = hkdf(shared, salt: share + theirs.rawRepresentation, info: x25519Info)
        let body = try aead(wrapKey, nonce: Data(count: 12), seal: key)
        var head = intro + stanzas.reduce(Data(), +) + Data("-> X25519 \(b64(share))\n\(b64(body))\n".utf8)
        head += Data("---".utf8)
        let mac = Data(HMAC<SHA256>.authenticationCode(for: head, using: hkdf(key, salt: Data(), info: Data("header".utf8))))
        return head + Data(" \(b64(mac))\n".utf8)
    }

    /// The raw stanzas and the file key, after checking the header MAC.
    static func fileKey(header: Data, identities: [AgeIdentity]) throws -> (stanzas: [Data], key: Data) {
        guard header.starts(with: intro), header.last == 0x0A else { throw SealingError.malformedHeader }
        var lines = header.dropFirst(intro.count).split(separator: 0x0A, omittingEmptySubsequences: false).map { Data($0) }
        lines.removeLast()  // the empty string after the final newline
        var stanzas: [Data] = []
        var fileKey: Data?
        var index = 0
        while index < lines.count, lines[index].starts(with: Data("-> ".utf8)) {
            let opener = lines[index]
            var raw = opener + Data([0x0A])
            var body = Data()
            index += 1
            while index < lines.count {
                let part = lines[index]
                raw += part + Data([0x0A])
                body += try unb64(part)
                index += 1
                if part.count < 64 { break }
            }
            stanzas.append(raw)
            let args = opener.dropFirst(3).split(separator: 0x20, omittingEmptySubsequences: false).map { Data($0) }
            if fileKey == nil { fileKey = try unwrap(args: args, body: body, identities: identities) }
        }
        guard index == lines.count - 1, lines[index].starts(with: Data("--- ".utf8)) else { throw SealingError.malformedHeader }
        guard let fileKey else { throw SealingError.noMatchingKey }
        let signed = intro + stanzas.reduce(Data(), +) + Data("---".utf8)
        let expected = Data(HMAC<SHA256>.authenticationCode(for: signed, using: hkdf(fileKey, salt: Data(), info: Data("header".utf8))))
        guard try unb64(lines[index].dropFirst(4)) == expected else { throw SealingError.headerAltered }
        return (stanzas, fileKey)
    }

    // MARK: Internals

    static func open(read: (Int) throws -> Data, write: (Data) throws -> Void, with identities: [AgeIdentity]) throws {
        // The header is a few hundred bytes per recipient; read it line by line from a small buffer.
        var buffer = Data()
        var consumed = 0
        func line() throws -> Data {
            while true {
                if let newline = buffer[consumed...].firstIndex(of: 0x0A) {
                    defer { consumed = newline + 1 }
                    return buffer[consumed..<newline]
                }
                let more = try read(4096)
                guard !more.isEmpty, buffer.count < 64 * 1024 else { throw SealingError.malformedHeader }
                buffer += more
            }
        }
        guard try line() + Data([0x0A]) == intro else { throw SealingError.notSealed }
        var header = intro
        var fileKey: Data?
        var macLine = Data()
        while true {
            let stanza = try line()
            if stanza.starts(with: Data("---".utf8)) { macLine = stanza; break }
            guard stanza.starts(with: Data("-> ".utf8)) else { throw SealingError.malformedHeader }
            header += stanza + Data([0x0A])
            var body = Data()
            while true {
                let part = try line()
                header += part + Data([0x0A])
                body += try unb64(part)
                if part.count < 64 { break }
            }
            let args = stanza.dropFirst(3).split(separator: 0x20, omittingEmptySubsequences: false).map { Data($0) }
            if fileKey == nil { fileKey = try unwrap(args: args, body: body, identities: identities) }
        }
        guard macLine.starts(with: Data("--- ".utf8)) else { throw SealingError.malformedHeader }
        guard let fileKey else { throw SealingError.noMatchingKey }
        header += Data("---".utf8)
        let expected = Data(HMAC<SHA256>.authenticationCode(for: header, using: hkdf(fileKey, salt: Data(), info: Data("header".utf8))))
        guard try unb64(macLine.dropFirst(4)) == expected else { throw SealingError.headerAltered }

        // Whatever follows the header in the buffer is the start of the payload.
        var pending = Data(buffer[consumed...])
        func take(_ count: Int) throws -> Data {
            while pending.count < count {
                let more = try read(max(count - pending.count, 64 * 1024))
                if more.isEmpty { break }
                pending += more
            }
            let chunk = pending.prefix(count)
            pending = Data(pending.dropFirst(chunk.count))
            return Data(chunk)
        }
        let nonce = try take(16)
        guard nonce.count == 16 else { throw SealingError.payloadAltered }
        let payloadKey = hkdf(fileKey, salt: nonce, info: Data("payload".utf8))
        var counter: UInt64 = 0
        var chunk = try take(chunkSize + tagSize)
        while true {
            let following = chunk.count == chunkSize + tagSize ? try take(chunkSize + tagSize) : Data()
            let last = following.isEmpty
            guard let plain = try? aead(payloadKey, nonce: chunkNonce(counter, last: last), open: chunk),
                  !(plain.isEmpty && (counter > 0 || !last))
            else { throw SealingError.payloadAltered }
            try write(plain)
            if last { return }
            chunk = following
            counter += 1
        }
    }

    static func unwrap(args: [Data], body: Data, identities: [AgeIdentity]) throws -> Data? {
        guard args.count == 2, args[0] == Data("X25519".utf8) else { return nil }
        let share = try unb64(args[1])
        guard share.count == 32, body.count == 32 else { throw SealingError.malformedHeader }
        let theirs = try Curve25519.KeyAgreement.PublicKey(rawRepresentation: share)
        for identity in identities {
            guard let shared = try? identity.privateKey.sharedSecretFromKeyAgreement(with: theirs).withUnsafeBytes({ Data($0) }),
                  shared != Data(count: 32)
            else { continue }
            let key = hkdf(shared, salt: share + identity.privateKey.publicKey.rawRepresentation, info: x25519Info)
            if let fileKey = try? aead(key, nonce: Data(count: 12), open: body) { return fileKey }
        }
        return nil
    }

    static func hkdf(_ ikm: Data, salt: Data, info: Data) -> SymmetricKey {
        HKDF<SHA256>.deriveKey(inputKeyMaterial: SymmetricKey(data: ikm), salt: salt, info: info, outputByteCount: 32)
    }

    static func aead(_ key: SymmetricKey, nonce: Data, seal plaintext: Data) throws -> Data {
        let box = try ChaChaPoly.seal(plaintext, using: key, nonce: ChaChaPoly.Nonce(data: nonce))
        return box.ciphertext + box.tag
    }

    static func aead(_ key: SymmetricKey, nonce: Data, open sealed: Data) throws -> Data {
        guard sealed.count >= tagSize else { throw SealingError.payloadAltered }
        let box = try ChaChaPoly.SealedBox(nonce: ChaChaPoly.Nonce(data: nonce),
                                           ciphertext: sealed.dropLast(tagSize), tag: sealed.suffix(tagSize))
        return try ChaChaPoly.open(box, using: key)
    }

    static func chunkNonce(_ counter: UInt64, last: Bool) -> Data {
        var nonce = Data(count: 3)  // 11-byte big-endian counter, then the last-chunk flag
        withUnsafeBytes(of: counter.bigEndian) { nonce.append(contentsOf: $0) }
        nonce.append(last ? 1 : 0)
        return nonce
    }

    static func b64(_ data: Data) -> String {
        String(data.base64EncodedString().trimmingCharacters(in: CharacterSet(charactersIn: "=")))
    }

    static func unb64(_ data: Data) throws -> Data {
        guard let text = String(data: data, encoding: .ascii), !text.hasSuffix("="),
              let decoded = Data(base64Encoded: text + String(repeating: "=", count: (4 - text.count % 4) % 4)),
              b64(decoded) == text
        else { throw SealingError.malformedHeader }
        return decoded
    }

    static func randomBytes(_ count: Int) -> Data {
        var generator = SystemRandomNumberGenerator()
        return Data((0..<count).map { _ in UInt8.random(in: .min ... .max, using: &generator) })
    }
}
