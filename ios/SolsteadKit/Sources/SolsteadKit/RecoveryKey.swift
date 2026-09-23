import Foundation

/// The owner's recovery key (ADR 0003): an age identity shown once at setup as a printable code
/// and saved as a standard age identity file, then forgotten by the phone. Only its public half
/// is enrolled on the controller.
public struct RecoveryKey: Sendable {
    public static let prefix = "AGE-SECRET-KEY-1"
    public static let groupLength = 5

    public let identity: AgeIdentity

    public init(identity: AgeIdentity = AgeIdentity()) { self.identity = identity }

    /// The full key, exactly as `age` reads it.
    public var code: String { identity.string }
    public var recipient: String { identity.recipient }

    /// The part after the fixed prefix, in groups of five for printing and typing back.
    public var groups: [String] {
        let body = Array(code.dropFirst(Self.prefix.count))
        return stride(from: 0, to: body.count, by: Self.groupLength).map {
            String(body[$0..<min($0 + Self.groupLength, body.count)])
        }
    }

    /// Two different group positions to ask the owner to type back, as proof they kept a copy.
    public static func challenge(groupCount: Int) -> [Int] {
        Array((0..<groupCount).shuffled().prefix(2)).sorted()
    }

    /// Whether `typed` matches group `index`, ignoring case and spaces.
    public func matches(group index: Int, typed: String) -> Bool {
        guard groups.indices.contains(index) else { return false }
        return Self.normalized(typed) == groups[index]
    }

    /// A standard age identity file: comment lines, then the key. `age -d -i` accepts it as-is.
    public func fileContents(controllerName: String, created: Date = Date()) -> String {
        let date = ISO8601DateFormatter.string(from: created, timeZone: .current, formatOptions: [.withFullDate])
        return """
        # Solstead recovery key for \(controllerName), created \(date).
        # Opens every recording and backup from this system if your phones are lost.
        # Keep it private and somewhere safe. With the free `age` tool: age -d -i <this file> <recording>.age
        # public key: \(recipient)
        \(code)

        """
    }

    /// Reads a recovery key the owner typed (with or without the prefix, spaces or line breaks)
    /// or the contents of a saved key file.
    public static func parse(_ text: String) throws -> AgeIdentity {
        if let line = text.split(whereSeparator: \.isNewline).first(where: {
            $0.trimmingCharacters(in: .whitespaces).uppercased().hasPrefix(prefix)
        }) {
            return try AgeIdentity(string: normalized(String(line)))
        }
        var compact = normalized(text)
        if !compact.hasPrefix(prefix) { compact = prefix + compact }
        return try AgeIdentity(string: compact)
    }

    static func normalized(_ text: String) -> String {
        text.uppercased().filter { !$0.isWhitespace }
    }
}
