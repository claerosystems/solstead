import Foundation

/// A camera request the owner is waiting on (live view, a snapshot, or a manual recording),
/// as shown on the wait card. The controller reports no progress, so only steps that really
/// have a fixed length get a progress bar.
public struct CameraActivity: Equatable, Sendable {
    public enum Kind: Equatable, Sendable { case live, snapshot, recording }

    public enum Phase: Equatable, Sendable {
        case working(step: String, countdown: Countdown?)
        case succeeded(String)
        case failed(String)
    }

    /// A step whose length is known in advance, such as filming the next ten seconds.
    public struct Countdown: Equatable, Sendable {
        public let start: Date
        public let seconds: TimeInterval
        /// The step shown once the countdown has run out but the request has not finished.
        public let then: String

        public init(start: Date, seconds: TimeInterval, then: String) {
            self.start = start
            self.seconds = seconds
            self.then = then
        }

        /// How far through the countdown `date` is, from 0 to 1.
        public func fraction(at date: Date) -> Double {
            guard seconds > 0 else { return 1 }
            return min(max(date.timeIntervalSince(start) / seconds, 0), 1)
        }
    }

    public let kind: Kind
    public let started: Date
    public var phase: Phase

    public init(kind: Kind, started: Date = Date(), phase: Phase) {
        self.kind = kind
        self.started = started
        self.phase = phase
    }

    public static func live(at date: Date = Date()) -> CameraActivity {
        CameraActivity(kind: .live, started: date, phase: .working(step: "Starting the camera stream…", countdown: nil))
    }

    public static func snapshot(at date: Date = Date()) -> CameraActivity {
        CameraActivity(kind: .snapshot, started: date, phase: .working(step: "Capturing a full-resolution frame…", countdown: nil))
    }

    /// With the stream buffer running the controller saves footage it already has, which is
    /// quick; without it the controller films the next `seconds`, so that wait has a real length.
    public static func recording(bufferRunning: Bool, seconds: Int = 10, at date: Date = Date()) -> CameraActivity {
        let phase: Phase = bufferRunning
            ? .working(step: "Saving the last \(seconds) seconds…", countdown: nil)
            : .working(step: "Recording the next \(seconds) seconds…",
                       countdown: Countdown(start: date, seconds: TimeInterval(seconds), then: "Saving the clip…"))
        return CameraActivity(kind: .recording, started: date, phase: phase)
    }

    public var isWorking: Bool {
        if case .working = phase { return true }
        return false
    }

    /// The status line after the owner cancels. Cancelling only stops the phone waiting.
    public var cancelledMessage: String {
        switch kind {
        case .live: return "Live view cancelled."
        case .snapshot: return "Snapshot cancelled."
        case .recording: return "Recording cancelled. The controller may still save the clip; refresh to see it under Recordings."
        }
    }
}
