import XCTest
@testable import SolsteadKit

final class CameraActivityTests: XCTestCase {
    let start = Date(timeIntervalSince1970: 1_000)

    func testBufferedRecordingHasNoCountdown() {
        let activity = CameraActivity.recording(bufferRunning: true, at: start)
        XCTAssertEqual(activity.phase, .working(step: "Saving the last 10 seconds…", countdown: nil))
    }

    func testUnbufferedRecordingCountsDownTheClipLength() throws {
        let activity = CameraActivity.recording(bufferRunning: false, seconds: 10, at: start)
        guard case let .working(step, countdown?) = activity.phase else { return XCTFail("expected a countdown") }
        XCTAssertEqual(step, "Recording the next 10 seconds…")
        XCTAssertEqual(countdown.then, "Saving the clip…")
        XCTAssertEqual(countdown.fraction(at: start), 0)
        XCTAssertEqual(countdown.fraction(at: start.addingTimeInterval(5)), 0.5, accuracy: 0.0001)
        XCTAssertEqual(countdown.fraction(at: start.addingTimeInterval(30)), 1)
        XCTAssertEqual(countdown.fraction(at: start.addingTimeInterval(-3)), 0)
    }

    func testCancellingARecordingWarnsTheClipMayStillBeSaved() {
        XCTAssertTrue(CameraActivity.recording(bufferRunning: true).cancelledMessage.contains("may still save"))
        XCTAssertEqual(CameraActivity.snapshot().cancelledMessage, "Snapshot cancelled.")
        XCTAssertEqual(CameraActivity.live().cancelledMessage, "Live view cancelled.")
    }

    func testOnlyWorkingPhasesAreWorking() {
        var activity = CameraActivity.snapshot(at: start)
        XCTAssertTrue(activity.isWorking)
        activity.phase = .failed("Camera is not configured")
        XCTAssertFalse(activity.isWorking)
        activity.phase = .succeeded("Saved")
        XCTAssertFalse(activity.isWorking)
    }
}
