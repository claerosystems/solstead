import AVFoundation
import CoreGraphics
import ImageIO
import UniformTypeIdentifiers
import XCTest
@testable import SolsteadKit

/// Answers by path, so concurrent frame downloads can arrive in any order.
final class RoutingTransport: ControllerTransport, @unchecked Sendable {
    private let lock = NSLock()
    var routes: [String: (status: Int, body: Data)] = [:]
    private(set) var requests: [URLRequest] = []

    func send(_ request: URLRequest) async throws -> (Data, URLResponse) {
        let key = "\(request.httpMethod ?? "GET") \(request.url!.path)"
        let route: (status: Int, body: Data) = lock.withLock {
            requests.append(request)
            return routes[key] ?? (404, Data(#"{"error": "Not found"}"#.utf8))
        }
        return (route.body, HTTPURLResponse(url: request.url!, statusCode: route.status, httpVersion: "HTTP/1.1", headerFields: nil)!)
    }
}

final class TimeLapseAssemblerTests: XCTestCase {
    let day = "2026-09-22"
    var transport = RoutingTransport()
    var client: ControllerClient!

    override func setUpWithError() throws {
        transport = RoutingTransport()
        let pairing = try Pairing(url: "https://solstead.local:8443", token: String(repeating: "k", count: 32),
                                  fingerprint: String(repeating: "cd", count: 32))
        client = ControllerClient(pairing: pairing, transport: transport)
    }

    /// A solid-colour JPEG; odd sizes check the even-size rounding.
    func jpeg(width: Int = 161, height: Int = 91, shade: CGFloat) throws -> Data {
        let context = try XCTUnwrap(CGContext(data: nil, width: width, height: height, bitsPerComponent: 8, bytesPerRow: 0,
                                              space: CGColorSpaceCreateDeviceRGB(),
                                              bitmapInfo: CGImageAlphaInfo.noneSkipLast.rawValue))
        context.setFillColor(red: shade, green: 0.5, blue: 1 - shade, alpha: 1)
        context.fill(CGRect(x: 0, y: 0, width: width, height: height))
        let image = try XCTUnwrap(context.makeImage())
        let data = NSMutableData()
        let destination = try XCTUnwrap(CGImageDestinationCreateWithData(data, UTType.jpeg.identifier as CFString, 1, nil))
        CGImageDestinationAddImage(destination, image, nil)
        XCTAssertTrue(CGImageDestinationFinalize(destination))
        return data as Data
    }

    /// Serves a day of frames; `sealTo` seals every other one, as after turning encryption on mid-day.
    func serveDay(count: Int, sealTo recipients: [String]? = nil, missing: Set<Int> = []) throws {
        var listed: [String] = []
        for index in 0..<count {
            let time = String(format: "09%02d00", index)
            let sealed = recipients != nil && index % 2 == 1
            let name = time + (sealed ? ".jpg.age" : ".jpg")
            var body = try jpeg(shade: CGFloat(index) / CGFloat(count))
            if sealed, let recipients { body = try Age.seal(body, to: recipients) }
            listed.append(#"{"day": "\#(day)", "name": "\#(name)", "unix": \#(1790060000 + index), "size_bytes": \#(body.count), "encrypted": \#(sealed)}"#)
            if !missing.contains(index) { transport.routes["GET /v1/timelapse/\(day)/\(name)"] = (200, body) }
        }
        transport.routes["GET /v1/timelapse/\(day)"] = (200, Data(#"{"date": "\#(day)", "frames": [\#(listed.joined(separator: ","))]}"#.utf8))
    }

    func videoTrack(at url: URL) async throws -> (duration: Double, size: CGSize, frames: Int) {
        let asset = AVURLAsset(url: url)
        let tracks = try await asset.loadTracks(withMediaType: .video)
        let track = try XCTUnwrap(tracks.first)
        let size = try await track.load(.naturalSize)
        let duration = try await asset.load(.duration).seconds
        let reader = try AVAssetReader(asset: asset)
        let output = AVAssetReaderTrackOutput(track: track, outputSettings: nil)
        reader.add(output)
        reader.startReading()
        var frames = 0
        while let sample = output.copyNextSampleBuffer() { if CMSampleBufferGetNumSamples(sample) > 0 { frames += 1 } }
        return (duration, size, frames)
    }

    func testAssemblesMixedPlainAndSealedFramesInOrderOnThePhone() async throws {
        let phone = AgeIdentity(), recovery = AgeIdentity()
        try serveDay(count: 12, sealTo: [phone.recipient, recovery.recipient])
        let seen = ProgressLog()
        let url = try await client.assembleTimelapse(day: day, fps: 12, identities: [phone]) { done, total in
            await seen.add(done, total)
        }
        defer { try? FileManager.default.removeItem(at: url) }
        XCTAssertEqual(url.deletingLastPathComponent(), ControllerClient.clipFolder)
        let video = try await videoTrack(at: url)
        XCTAssertEqual(video.frames, 12)
        XCTAssertEqual(video.duration, 1.0, accuracy: 0.01)
        XCTAssertEqual(video.size, CGSize(width: 160, height: 90))  // rounded down to even
        let progress = await seen.entries
        XCTAssertEqual(progress.last?.0, 12)
        XCTAssertEqual(progress.map(\.1), Array(repeating: 12, count: progress.count))
        // Only the frame list and the frames were fetched; never the controller's video.
        XCTAssertFalse(transport.requests.contains { $0.url!.path.hasSuffix("video.mp4") })
    }

    func testFramesPrunedMeanwhileAreSkipped() async throws {
        try serveDay(count: 5, missing: [2])
        let url = try await client.assembleTimelapse(day: day, fps: 5, identities: [])
        defer { try? FileManager.default.removeItem(at: url) }
        let video = try await videoTrack(at: url)
        XCTAssertEqual(video.frames, 4)
    }

    func testSealedDayNeedsAKeyThatOpensIt() async throws {
        let other = AgeIdentity()
        try serveDay(count: 4, sealTo: [AgeIdentity().recipient])
        do {
            _ = try await client.assembleTimelapse(day: day, fps: 12, identities: [])
            XCTFail("expected no key")
        } catch SealingError.noMatchingKey {}
        do {
            _ = try await client.assembleTimelapse(day: day, fps: 12, identities: [other])
            XCTFail("expected no matching key")
        } catch SealingError.noMatchingKey {}
        // Nothing half-written is left behind.
        let leftover = ControllerClient.clipFolder.appendingPathComponent("timelapse-\(day).mp4")
        XCTAssertFalse(FileManager.default.fileExists(atPath: leftover.path))
    }

    func testEmptyDayIsAnError() async throws {
        transport.routes["GET /v1/timelapse/\(day)"] = (200, Data(#"{"date": "\#(day)", "frames": []}"#.utf8))
        do {
            _ = try await client.assembleTimelapse(day: day, fps: 12, identities: [])
            XCTFail("expected no frames")
        } catch TimeLapseAssemblyError.noFrames {}
    }

    func testDayListingsDecodeEncryptedCounts() throws {
        let json = Data(#"{"date": "2026-09-22", "frames": 3, "bytes": 900, "encrypted_frames": 1}"#.utf8)
        XCTAssertTrue(try ControllerJSON.decoder.decode(TimeLapseDay.self, from: json).isEncrypted)
        let older = Data(#"{"date": "2026-09-22", "frames": 3, "bytes": 900}"#.utf8)
        XCTAssertFalse(try ControllerJSON.decoder.decode(TimeLapseDay.self, from: older).isEncrypted)
        let clip = Data(#"{"id": "a.mp4.age", "size_bytes": 10, "created_unix": 1, "encrypted": true}"#.utf8)
        XCTAssertTrue(try ControllerJSON.decoder.decode(Recording.self, from: clip).isEncrypted)
    }

    func testRestoringAccessRewrapsADaysFrameHeadersInBatches() async throws {
        let oldPhone = AgeIdentity(), recovery = AgeIdentity(), newPhone = AgeIdentity()
        func header(_ recipients: [String]) throws -> Data {
            let sealed = try Age.seal(Data("frame".utf8), to: recipients)
            let end = sealed.range(of: Data("\n--- ".utf8))!.upperBound
            return sealed[..<(sealed[end...].firstIndex(of: 0x0A)! + 1)]
        }
        let headers: [String: Data] = [
            "090000.jpg.age": try header([oldPhone.recipient, recovery.recipient]),
            "090500.jpg.age": try header([oldPhone.recipient, recovery.recipient]),
            "091000.jpg.age": try header([newPhone.recipient, recovery.recipient]),  // already opens
            "091500.jpg.age": try header([oldPhone.recipient, AgeIdentity().recipient]),  // earlier recovery key
        ]
        let listed = headers.map { #""\#($0.key)": "\#($0.value.base64EncodedString())""# }.joined(separator: ",")
        transport.routes["GET /v1/timelapse/\(day)/headers"] = (200, Data(#"{"date": "\#(day)", "headers": {\#(listed)}}"#.utf8))
        transport.routes["PUT /v1/timelapse/\(day)/headers"] = (200, Data(#"{"date": "\#(day)", "replaced": 1}"#.utf8))

        let count = try await client.rewrapTimelapseDay(day, using: recovery, adding: newPhone, batch: 1)
        XCTAssertEqual(count.already, 1)
        XCTAssertEqual(count.unreadable, 1)
        XCTAssertEqual(count.unlocked, 2)
        let puts = transport.requests.filter { $0.httpMethod == "PUT" }
        XCTAssertEqual(puts.count, 2)  // one batch per frame with batch: 1
        var sent: [String: Data] = [:]
        for put in puts {
            let body = try JSONDecoder().decode([String: [String: Data]].self, from: put.httpBody!)
            sent.merge(body["headers"]!) { a, _ in a }
        }
        XCTAssertEqual(Set(sent.keys), ["090000.jpg.age", "090500.jpg.age"])
        for (name, rewrapped) in sent {
            XCTAssertTrue(Age.canOpen(header: rewrapped, with: [newPhone]), name)
            XCTAssertTrue(Age.canOpen(header: rewrapped, with: [oldPhone]), name)
            XCTAssertTrue(rewrapped.count > headers[name]!.count)
        }
    }
}

actor ProgressLog {
    private(set) var entries: [(Int, Int)] = []
    func add(_ done: Int, _ total: Int) { entries.append((done, total)) }
}
