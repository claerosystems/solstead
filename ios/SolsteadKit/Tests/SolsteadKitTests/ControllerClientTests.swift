import XCTest
@testable import SolsteadKit

/// Serves canned responses and records what the client sent.
final class StubTransport: ControllerTransport, @unchecked Sendable {
    var responses: [(status: Int, body: Data)] = []
    var requests: [URLRequest] = []

    func send(_ request: URLRequest) async throws -> (Data, URLResponse) {
        requests.append(request)
        let next = responses.isEmpty ? (status: 500, body: Data()) : responses.removeFirst()
        let response = HTTPURLResponse(url: request.url!, statusCode: next.status, httpVersion: "HTTP/1.1", headerFields: nil)!
        return (next.body, response)
    }
}

final class ControllerClientTests: XCTestCase {
    let statusJSON = Data("""
    {"name": "Solstead", "version": "0.1.0", "uptime_seconds": 61, "temperature_c": 41.5,
     "storage": {"ready": true, "total_bytes": 1000, "free_bytes": 400, "used_bytes": 600},
     "camera": {"configured": true, "last_success_unix": 1758500000},
     "recording_mode": "manual", "remote_access": false}
    """.utf8)

    var transport = StubTransport()
    var client: ControllerClient!

    override func setUpWithError() throws {
        transport = StubTransport()
        let pairing = try Pairing(url: "https://solstead.local:8443", token: String(repeating: "k", count: 32),
                                  fingerprint: String(repeating: "cd", count: 32))
        client = ControllerClient(pairing: pairing, transport: transport)
    }

    func testStatusRequestAndDecoding() async throws {
        transport.responses = [(200, statusJSON)]
        let status = try await client.status()
        XCTAssertEqual(status.name, "Solstead")
        XCTAssertEqual(status.uptimeSeconds, 61)
        XCTAssertEqual(status.temperatureC, 41.5)
        XCTAssertEqual(status.storage.freeBytes, 400)
        XCTAssertEqual(status.camera.lastSuccessUnix, 1758500000)
        let request = try XCTUnwrap(transport.requests.first)
        XCTAssertEqual(request.url?.absoluteString, "https://solstead.local:8443/v1/status")
        XCTAssertEqual(request.httpMethod, "GET")
        XCTAssertEqual(request.value(forHTTPHeaderField: "Authorization"), "Bearer " + String(repeating: "k", count: 32))
    }

    func testStatusWithUnavailableStorage() async throws {
        let json = Data("""
        {"name": "S", "version": "0.1.0", "uptime_seconds": 1, "temperature_c": null,
         "storage": {"ready": false, "error": "Recording SSD is not mounted"},
         "camera": {"configured": false, "last_success_unix": null},
         "recording_mode": "manual", "remote_access": false}
        """.utf8)
        transport.responses = [(200, json)]
        let status = try await client.status()
        XCTAssertFalse(status.storage.ready)
        XCTAssertEqual(status.storage.error, "Recording SSD is not mounted")
        XCTAssertNil(status.temperatureC)
    }

    func testRecordingsAndRecord() async throws {
        transport.responses = [
            (201, Data(#"{"id": "a.mp4", "size_bytes": 12, "created_unix": 1758500001}"#.utf8)),
            (200, Data(#"{"recordings": [{"id": "a.mp4", "size_bytes": 12, "created_unix": 1758500001}]}"#.utf8)),
        ]
        let clip = try await client.record()
        XCTAssertEqual(clip.id, "a.mp4")
        XCTAssertEqual(clip.created, Date(timeIntervalSince1970: 1758500001))
        XCTAssertEqual(transport.requests[0].httpMethod, "POST")
        XCTAssertEqual(transport.requests[0].url?.path, "/v1/recordings")
        let listed = try await client.recordings()
        XCTAssertEqual(listed, [clip])
    }

    func testStatusEventsBlockIsOptional() async throws {
        transport.responses = [(200, statusJSON)]
        let status = try await client.status()
        XCTAssertNil(status.events)
        let withEvents = statusJSON.dropLast(1) + Data("""
        , "events": {"enabled": true, "running": true, "reason": null, "buffer_running": true,
          "buffer_age_seconds": 1.5, "motion_active": false, "camera_subscribed": true, "camera_error": null,
          "last_event_unix": 1758500100, "last_error": null, "count": 3, "used_bytes": 1000, "allocation_bytes": 2000}}
        """.utf8).dropFirst(0)
        transport.responses = [(200, Data(withEvents))]
        let events = try await client.status().events
        XCTAssertEqual(events?.count, 3)
        XCTAssertEqual(events?.bufferAgeSeconds, 1.5)
        XCTAssertEqual(events?.cameraSubscribed, true)
    }

    func testEventsAndClipDownload() async throws {
        let eventJSON = #"{"id": "20260922T140005-abcdef01", "started_unix": 1790085605, "ended_unix": 1790085615, "clip_from_unix": 1790085595, "clip_to_unix": 1790085645, "size_bytes": 7, "trigger": "tns1:RuleEngine/CellMotionDetector/Motion", "created_unix": 1790085650, "duration_seconds": 50}"#
        transport.responses = [
            (200, Data(#"{"events": [\#(eventJSON)]}"#.utf8)),
            (200, Data("mp4data".utf8)),
        ]
        let events = try await client.events()
        XCTAssertEqual(events.count, 1)
        let event = try XCTUnwrap(events.first)
        XCTAssertEqual(event.activitySeconds, 10)
        XCTAssertEqual(event.durationSeconds, 50)
        XCTAssertEqual(transport.requests[0].url?.path, "/v1/events")
        let url = try await client.downloadEventClip(event)
        XCTAssertEqual(transport.requests[1].url?.path, "/v1/events/20260922T140005-abcdef01/clip")
        XCTAssertEqual(try Data(contentsOf: url), Data("mp4data".utf8))
        try? FileManager.default.removeItem(at: url)
    }

    func testLiveStartStopAndResources() async throws {
        transport.responses = [
            (200, Data(#"{"available": true, "active": true, "quality": "high", "ready": true, "playlist": "/v1/live/index.m3u8"}"#.utf8)),
            (200, Data("#EXTM3U\n".utf8)),
            (200, Data(#"{"active": false}"#.utf8)),
        ]
        let info = try await client.startLive(quality: .high)
        XCTAssertEqual(info.quality, "high")
        XCTAssertEqual(transport.requests[0].httpMethod, "POST")
        XCTAssertEqual(transport.requests[0].url?.absoluteString, "https://solstead.local:8443/v1/live?quality=high")
        let playlist = try await client.liveResource("index.m3u8")
        XCTAssertEqual(String(decoding: playlist, as: UTF8.self), "#EXTM3U\n")
        XCTAssertEqual(transport.requests[1].url?.path, "/v1/live/index.m3u8")
        try await client.stopLive()
        XCTAssertEqual(transport.requests[2].httpMethod, "DELETE")
    }

    func testStatusLiveBlockIsOptional() async throws {
        transport.responses = [(200, statusJSON)]
        let status = try await client.status()
        XCTAssertNil(status.live)
    }

    func testTimelapseStatusUpdateAndVideo() async throws {
        let statusJSON = #"{"enabled": true, "interval_seconds": 300, "window": "06:00-18:00", "width": 1920, "allocation_bytes": 4294967296, "export_fps": 12, "day_count": 1, "frames": 3, "used_bytes": 300000, "frames_per_day": 144, "estimated_retention_days": 200, "oldest_date": "2026-09-22", "newest_unix": 1790085605, "last_error": null, "days": [{"date": "2026-09-22", "frames": 3, "bytes": 300000}]}"#
        transport.responses = [
            (200, Data(statusJSON.utf8)),
            (200, Data(statusJSON.replacingOccurrences(of: "\"interval_seconds\": 300", with: "\"interval_seconds\": 60").utf8)),
            (200, Data("mp4".utf8)),
        ]
        let status = try await client.timelapse()
        XCTAssertEqual(status.days?.first?.frames, 3)
        XCTAssertEqual(status.windowStart, "06:00")
        XCTAssertEqual(status.windowEnd, "18:00")
        XCTAssertEqual(status.estimatedRetentionDays, 200)
        let updated = try await client.updateTimelapse(TimeLapseSettingsUpdate(intervalSeconds: 60, window: "07:00-17:00"))
        XCTAssertEqual(updated.intervalSeconds, 60)
        let put = transport.requests[1]
        XCTAssertEqual(put.httpMethod, "PUT")
        XCTAssertEqual(put.value(forHTTPHeaderField: "Content-Type"), "application/json")
        let sent = try XCTUnwrap(put.httpBody.flatMap { try? JSONSerialization.jsonObject(with: $0) as? [String: Any] })
        XCTAssertEqual(sent["interval_seconds"] as? Int, 60)
        XCTAssertEqual(sent["window"] as? String, "07:00-17:00")
        XCTAssertNil(sent["enabled"])
        let url = try await client.downloadTimelapseVideo(day: "2026-09-22")
        XCTAssertEqual(transport.requests[2].url?.path, "/v1/timelapse/2026-09-22/video.mp4")
        XCTAssertEqual(try Data(contentsOf: url), Data("mp4".utf8))
    }

    func testThermalBlock() async throws {
        let json = statusJSON.dropLast(1) + Data(#", "thermal": {"temperature_c": 76.5, "warning_c": 75.0, "throttle_c": 80.0, "flags_available": true, "undervoltage_now": false, "throttled_now": false, "undervoltage_since_boot": true, "throttled_since_boot": false, "level": "warning"}}"#.utf8)
        transport.responses = [(200, Data(json))]
        let thermal = try await client.status().thermal
        XCTAssertEqual(thermal?.temperatureC, 76.5)
        XCTAssertEqual(thermal?.level, "warning")
        XCTAssertEqual(thermal?.undervoltageSinceBoot, true)
    }

    func testHealthLogAndStatusBlock() async throws {
        transport.responses = [
            (200, Data(#"{"entries": [{"unix": 1790085605, "level": "warning", "kind": "camera", "message": "Camera stream ended; restarting the buffer", "restarts": 1}]}"#.utf8)),
            (200, statusJSON.dropLast(1) + Data(#", "health": {"clock_ok": true, "last_day": {"info": 4, "warning": 1, "error": 0}, "latest": []}}"#.utf8)),
        ]
        let entries = try await client.health(minimumLevel: "warning", limit: 20)
        XCTAssertEqual(entries.first?.kind, "camera")
        XCTAssertEqual(transport.requests[0].url?.query, "level=warning&limit=20")
        let health = try await client.status().health
        XCTAssertEqual(health?.lastDay.warning, 1)
        XCTAssertEqual(health?.clockOk, true)
    }

    func testSnapshotReturnsRawBytes() async throws {
        transport.responses = [(200, Data([0xFF, 0xD8, 0x01]))]
        let data = try await client.snapshot()
        XCTAssertEqual(data, Data([0xFF, 0xD8, 0x01]))
        XCTAssertEqual(transport.requests[0].url?.path, "/v1/camera/snapshot")
    }

    func testControllerErrorMessageIsSurfaced() async {
        transport.responses = [(503, Data(#"{"error": "Camera capture is busy; try again shortly"}"#.utf8))]
        do {
            _ = try await client.snapshot()
            XCTFail("expected error")
        } catch {
            XCTAssertEqual(error as? ControllerError, .controller("Camera capture is busy; try again shortly", status: 503))
            XCTAssertEqual(error.localizedDescription, "Camera capture is busy; try again shortly")
        }
    }

    func testUnexpectedStatusWithoutBody() async {
        transport.responses = [(502, Data("<html>".utf8))]
        do {
            _ = try await client.status()
            XCTFail("expected error")
        } catch {
            XCTAssertEqual(error as? ControllerError, .unexpectedResponse(status: 502))
        }
    }
}
