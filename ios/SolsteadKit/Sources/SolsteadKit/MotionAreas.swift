import Foundation

/// A corner of a motion area, in fractions of the camera picture: (0, 0) is the top left and
/// (1, 1) the bottom right, so areas fit any stream resolution.
public struct MotionPoint: Codable, Equatable, Hashable, Sendable {
    public var x: Double
    public var y: Double

    public init(x: Double, y: Double) {
        self.x = x
        self.y = y
    }

    /// The same point kept inside the picture.
    public var clamped: MotionPoint {
        MotionPoint(x: min(max(x, 0), 1), y: min(max(y, 0), 1))
    }

    // The controller sends and expects [x, y] pairs.
    public init(from decoder: Decoder) throws {
        var container = try decoder.unkeyedContainer()
        x = try container.decode(Double.self)
        y = try container.decode(Double.self)
    }

    public func encode(to encoder: Encoder) throws {
        var container = encoder.unkeyedContainer()
        try container.encode((x * 10_000).rounded() / 10_000)
        try container.encode((y * 10_000).rounded() / 10_000)
    }
}

/// An owner-drawn area of the picture where motion counts. Motion elsewhere is ignored.
public struct MotionArea: Codable, Equatable, Identifiable, Sendable {
    public static let maximumAreas = 8
    public static let minimumPoints = 3
    public static let maximumPoints = 16

    public var id = UUID()
    public var name: String
    public var points: [MotionPoint]

    public init(name: String, points: [MotionPoint]) {
        self.name = name
        self.points = points
    }

    enum CodingKeys: String, CodingKey { case name, points }

    /// A new rectangular area in the middle of the picture, for the owner to reshape.
    public static func rectangle(named name: String, center: MotionPoint = MotionPoint(x: 0.5, y: 0.5),
                                 width: Double = 0.4, height: Double = 0.4) -> MotionArea {
        let left = min(max(center.x - width / 2, 0), 1 - width)
        let top = min(max(center.y - height / 2, 0), 1 - height)
        return MotionArea(name: name, points: [
            MotionPoint(x: left, y: top), MotionPoint(x: left + width, y: top),
            MotionPoint(x: left + width, y: top + height), MotionPoint(x: left, y: top + height),
        ])
    }

    /// Even-odd point-in-polygon test, matching the controller's.
    public func contains(_ point: MotionPoint) -> Bool {
        var inside = false
        var j = points.count - 1
        for i in points.indices {
            let a = points[i], b = points[j]
            if (a.y > point.y) != (b.y > point.y),
               point.x < (b.x - a.x) * (point.y - a.y) / (b.y - a.y) + a.x {
                inside.toggle()
            }
            j = i
        }
        return inside
    }

    /// Fraction of the picture the area covers.
    public var area: Double {
        var total = 0.0
        for i in points.indices {
            let a = points[i], b = points[(i + 1) % points.count]
            total += a.x * b.y - b.x * a.y
        }
        return abs(total) / 2
    }

    /// Midpoint of the edge from corner `index` to the next, where a new corner can be added.
    public func midpoint(after index: Int) -> MotionPoint {
        let a = points[index], b = points[(index + 1) % points.count]
        return MotionPoint(x: (a.x + b.x) / 2, y: (a.y + b.y) / 2)
    }

    /// Moves every corner by (dx, dy) without letting the area leave the picture.
    public func moved(from original: [MotionPoint], dx: Double, dy: Double) -> MotionArea {
        let xs = original.map(\.x), ys = original.map(\.y)
        let dx = min(max(dx, -(xs.min() ?? 0)), 1 - (xs.max() ?? 1))
        let dy = min(max(dy, -(ys.min() ?? 0)), 1 - (ys.max() ?? 1))
        var copy = self
        copy.points = original.map { MotionPoint(x: $0.x + dx, y: $0.y + dy) }
        return copy
    }
}

public enum MotionSensitivity: String, Codable, CaseIterable, Sendable {
    case low, medium, high
}

/// What the controller's motion checker is doing, from `GET /v1/motion`.
public struct MotionCheck: Decodable, Equatable, Sendable {
    public let checking: Bool
    public let confirmed: Int
    public let ignored: Int
    public let passedThrough: Int
    public let available: Bool?
    public let running: Bool?
    public let seeingMotion: Bool?
    public let lastError: String?
}

/// The owner's motion areas and shadow filtering, stored on the controller.
public struct MotionSettings: Codable, Equatable, Sendable {
    public var zones: [MotionArea]
    public var ignoreShadows: Bool
    public var sensitivity: MotionSensitivity
    public var verify: Bool
    public var check: MotionCheck?

    public init(zones: [MotionArea] = [], ignoreShadows: Bool = true, sensitivity: MotionSensitivity = .medium,
                verify: Bool = true, check: MotionCheck? = nil) {
        self.zones = zones
        self.ignoreShadows = ignoreShadows
        self.sensitivity = sensitivity
        self.verify = verify
        self.check = check
    }

    enum CodingKeys: String, CodingKey { case zones, ignoreShadows, sensitivity, verify, check }

    public func encode(to encoder: Encoder) throws {
        var container = encoder.container(keyedBy: CodingKeys.self)
        try container.encode(zones, forKey: .zones)
        try container.encode(ignoreShadows, forKey: .ignoreShadows)
        try container.encode(sensitivity, forKey: .sensitivity)
        try container.encode(verify, forKey: .verify)
    }

    /// Settings compared without the checker's live counts, to tell whether there are edits to save.
    public func sameChoices(as other: MotionSettings) -> Bool {
        zones.map(\.points) == other.zones.map(\.points) && zones.map(\.name) == other.zones.map(\.name)
            && ignoreShadows == other.ignoreShadows && sensitivity == other.sensitivity && verify == other.verify
    }
}
