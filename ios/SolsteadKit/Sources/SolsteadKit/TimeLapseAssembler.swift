import AVFoundation
import CoreGraphics
import Foundation
import ImageIO

public enum TimeLapseAssemblyError: LocalizedError, Equatable {
    case noFrames
    case unreadableFrame(String)
    case writerFailed(String)

    public var errorDescription: String? {
        switch self {
        case .noFrames: return "There are no time-lapse frames for that day."
        case let .unreadableFrame(name): return "Time-lapse frame \(name) is not a readable image."
        case let .writerFailed(reason): return "Could not make the time-lapse video: \(reason)"
        }
    }
}

/// Writes JPEG frames, in order, into an H.264 MP4 at a fixed frame rate. The first frame sets
/// the video size (rounded down to even numbers, as H.264 needs); later frames of another size
/// are scaled to it. This is the phone's counterpart of the controller's ffmpeg export, used for
/// days whose frames are sealed and so can only be decrypted here (ADR 0003).
public final class TimeLapseWriter {
    public let url: URL
    private let fps: Int32
    private let writer: AVAssetWriter
    private var input: AVAssetWriterInput?
    private var adaptor: AVAssetWriterInputPixelBufferAdaptor?
    private var width = 0, height = 0
    public private(set) var frameCount = 0

    /// Replaces any file already at `url`.
    public init(url: URL, fps: Int) throws {
        try? FileManager.default.removeItem(at: url)
        self.url = url
        self.fps = Int32(max(1, fps))
        writer = try AVAssetWriter(outputURL: url, fileType: .mp4)
        writer.shouldOptimizeForNetworkUse = true
    }

    public func append(jpeg: Data, name: String = "frame") async throws {
        guard let source = CGImageSourceCreateWithData(jpeg as CFData, nil),
              let image = CGImageSourceCreateImageAtIndex(source, 0, nil),
              image.width >= 2, image.height >= 2 else {
            throw TimeLapseAssemblyError.unreadableFrame(name)
        }
        if input == nil { try start(width: image.width & ~1, height: image.height & ~1) }
        guard let input, let adaptor else { return }
        while !input.isReadyForMoreMediaData {
            guard writer.status == .writing else { throw failure() }
            try await Task.sleep(nanoseconds: 2_000_000)
        }
        guard let pool = adaptor.pixelBufferPool else { throw failure() }
        var created: CVPixelBuffer?
        CVPixelBufferPoolCreatePixelBuffer(nil, pool, &created)
        guard let buffer = created else { throw TimeLapseAssemblyError.writerFailed("no pixel buffer") }
        CVPixelBufferLockBaseAddress(buffer, [])
        let context = CGContext(
            data: CVPixelBufferGetBaseAddress(buffer), width: width, height: height, bitsPerComponent: 8,
            bytesPerRow: CVPixelBufferGetBytesPerRow(buffer), space: CGColorSpaceCreateDeviceRGB(),
            bitmapInfo: CGImageAlphaInfo.noneSkipFirst.rawValue | CGBitmapInfo.byteOrder32Little.rawValue)
        context?.interpolationQuality = .high
        context?.draw(image, in: CGRect(x: 0, y: 0, width: width, height: height))
        CVPixelBufferUnlockBaseAddress(buffer, [])
        guard context != nil,
              adaptor.append(buffer, withPresentationTime: CMTime(value: CMTimeValue(frameCount), timescale: fps)) else {
            throw failure()
        }
        frameCount += 1
    }

    /// Finishes the file and returns its URL.
    public func finish() async throws -> URL {
        guard let input, frameCount > 0 else {
            cancel()
            throw TimeLapseAssemblyError.noFrames
        }
        input.markAsFinished()
        // End the session one frame after the last so the final frame is shown for its full time.
        writer.endSession(atSourceTime: CMTime(value: CMTimeValue(frameCount), timescale: fps))
        await writer.finishWriting()
        guard writer.status == .completed else { throw failure() }
        return url
    }

    /// Abandons the video and deletes whatever was written.
    public func cancel() {
        if writer.status == .writing { writer.cancelWriting() }
        try? FileManager.default.removeItem(at: url)
    }

    private func start(width: Int, height: Int) throws {
        self.width = width
        self.height = height
        let input = AVAssetWriterInput(mediaType: .video, outputSettings: [
            AVVideoCodecKey: AVVideoCodecType.h264,
            AVVideoWidthKey: width,
            AVVideoHeightKey: height,
        ])
        input.expectsMediaDataInRealTime = false
        let adaptor = AVAssetWriterInputPixelBufferAdaptor(assetWriterInput: input, sourcePixelBufferAttributes: [
            kCVPixelBufferPixelFormatTypeKey as String: kCVPixelFormatType_32BGRA,
            kCVPixelBufferWidthKey as String: width,
            kCVPixelBufferHeightKey as String: height,
        ])
        guard writer.canAdd(input) else { throw TimeLapseAssemblyError.writerFailed("unsupported video size") }
        writer.add(input)
        guard writer.startWriting() else { throw failure() }
        writer.startSession(atSourceTime: .zero)
        self.input = input
        self.adaptor = adaptor
    }

    private func failure() -> TimeLapseAssemblyError {
        .writerFailed(writer.error?.localizedDescription ?? "the video encoder stopped")
    }
}

extension ControllerClient {
    /// Frames fetched at once while assembling; enough to keep the connection busy.
    static let frameBatch = 6

    /// Assembles a day's time-lapse on the phone: downloads each frame, decrypts sealed ones with
    /// `identities`, and encodes them at `fps` into a temporary MP4 in `clipFolder`. Frames pruned
    /// on the controller meanwhile are skipped. `progress` gets (frames done, total).
    public func assembleTimelapse(day: String, fps: Int, identities: [AgeIdentity],
                                  progress: @escaping @Sendable (Int, Int) async -> Void = { _, _ in }) async throws -> URL {
        let frames = try await timelapseFrames(day: day)
        guard !frames.isEmpty else { throw TimeLapseAssemblyError.noFrames }
        if identities.isEmpty, frames.contains(where: \.isEncrypted) { throw SealingError.noMatchingKey }
        try FileManager.default.createDirectory(at: Self.clipFolder, withIntermediateDirectories: true)
        let writer = try TimeLapseWriter(url: Self.clipFolder.appendingPathComponent("timelapse-\(day).mp4"), fps: fps)
        do {
            var done = 0
            for start in stride(from: 0, to: frames.count, by: Self.frameBatch) {
                let batch = Array(frames[start..<min(start + Self.frameBatch, frames.count)])
                let images = try await withThrowingTaskGroup(of: (Int, Data?).self) { group in
                    for (index, frame) in batch.enumerated() {
                        group.addTask { (index, try await self.frameImage(frame, identities: identities)) }
                    }
                    var ordered = [Data?](repeating: nil, count: batch.count)
                    for try await (index, image) in group { ordered[index] = image }
                    return ordered
                }
                for (frame, image) in zip(batch, images) {
                    if let image { try await writer.append(jpeg: image, name: frame.name) }
                }
                done += batch.count
                await progress(done, frames.count)
            }
            return try await writer.finish()
        } catch {
            writer.cancel()
            throw error
        }
    }

    /// A frame's JPEG, decrypted on the phone if sealed; nil if it was pruned meanwhile.
    private func frameImage(_ frame: TimeLapseFrame, identities: [AgeIdentity]) async throws -> Data? {
        let data: Data
        do {
            data = try await timelapseFrame(frame)
        } catch let ControllerError.controller(_, status) where status == 404 {
            return nil
        }
        guard frame.isEncrypted || Age.isSealed(data.prefix(32)) else { return data }
        return try Age.open(data, with: identities)
    }
}
