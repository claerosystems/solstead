import AVKit
import Photos
import SwiftUI
import SolsteadKit

/// Screen state for the one paired controller.
@MainActor
final class ControllerModel: ObservableObject {
    // Owner-entered pairing fields.
    @Published var url = "https://solstead-r4b-1.local:8443"
    @Published var token = ""
    @Published var fingerprint = ""

    // What the controller last told us.
    @Published private(set) var status: ControllerStatus?
    @Published private(set) var recordings: [Recording] = []
    @Published private(set) var events: [Event] = []
    @Published private(set) var timelapse: TimeLapseStatus?
    @Published var playingClip: PlayableClip? {
        // The downloaded file goes once its player closes; exported copies are unaffected.
        didSet { if let old = oldValue, old.url != playingClip?.url { try? FileManager.default.removeItem(at: old.url) } }
    }
    @Published var liveSession: LiveSession?
    @Published var showSettings = false
    @Published var healthEntries: [HealthEntry]?  // non-nil while the health sheet is shown
    @Published private(set) var snapshot: Snapshot?
    @Published var message = "Connect to your controller on the same local network."
    @Published private(set) var busy = false
    /// The recovery key being shown during encryption setup; never stored.
    @Published var recoverySetup: RecoveryKey?

    private var client: ControllerClient?

    var isConnected: Bool { client != nil && status != nil }

    init() {
        if let saved = PairingStore.load() {
            url = saved.url.absoluteString
            token = saved.token
            fingerprint = saved.fingerprint
        } else if BenchPairing.fromEnvironment() == nil {
            showSettings = true  // nothing paired yet: start on the settings pane
        }
        if PairingStore.load() == nil, let bench = BenchPairing.fromEnvironment() {
            // Debug builds only: scripts/bench-controller.sh prints these values.
            url = bench.url
            token = bench.token
            fingerprint = bench.fingerprint
            Task { await connect() }
        }
    }

    func connect() async {
        await perform {
            let pairing = try Pairing(url: url, token: token, fingerprint: fingerprint)
            let candidate = ControllerClient.pinned(to: pairing)
            try await load(with: candidate)
            client = candidate
            do {
                try PairingStore.save(pairing)
                message = "Connected securely. Manual recording is ready."
            } catch {
                // Still connected; the owner will just have to re-enter the pairing next launch.
                message = "Connected securely, but the pairing was not saved: \(error.localizedDescription)"
            }
        } onFailure: {
            client = nil
            status = nil
            recordings = []
            events = []
            timelapse = nil
            snapshot = nil
        }
    }

    func showHealth() async {
        await perform {
            healthEntries = try await connected().health(minimumLevel: "info", limit: 200)
        }
    }

    func updateTimelapse(_ update: TimeLapseSettingsUpdate) async {
        await perform {
            timelapse = try await connected().updateTimelapse(update)
            message = "Time-lapse settings saved on the controller."
        }
    }

    func playTimelapse(_ day: TimeLapseDay) async {
        await perform {
            message = "Assembling and downloading the \(day.date) time-lapse…"
            let url = try await connected().downloadTimelapseVideo(day: day.date)
            playingClip = try PlayableClip(
                title: "Time-lapse \(day.date)", downloaded: url,
                exportName: "Solstead time-lapse \(day.date)", recorded: timelapseStart(of: day))
            message = "Playing time-lapse for \(day.date)."
        }
    }

    func startLive(quality: LiveQuality = .low) async {
        await perform {
            let client = try connected()
            message = "Starting live view…"
            let started = Date()
            let info = try await client.startLive(quality: quality)
            let proxy: LiveProxy
            if let existing = liveSession?.proxy {
                proxy = existing
            } else {
                proxy = try LiveProxy(client: client)
                try await proxy.start()
            }
            liveSession = LiveSession(quality: quality, info: info, proxy: proxy, requestedAt: started)
            message = "Live view running (\(quality.label.lowercased()) quality)."
        }
    }

    func stopLive() {
        liveSession?.proxy.stop()
        liveSession = nil
        Task { try? await client?.stopLive() }
    }

    func play(_ event: Event) async {
        await perform {
            message = "Downloading \(ByteCountFormatter.string(fromByteCount: event.sizeBytes, countStyle: .file)) clip…"
            let url = try await connected().downloadEventClip(event, identities: [DeviceKeyStore.load()].compactMap { $0 })
            playingClip = try PlayableClip(
                title: event.started.formatted(date: .abbreviated, time: .shortened), downloaded: url,
                exportName: "Solstead event \(PlayableClip.fileStamp.string(from: event.started))", recorded: event.started)
            message = "Playing event from \(event.started.formatted(date: .abbreviated, time: .shortened))."
        }
    }

    /// Exports a clip to the Photos library (add-only permission), dated to when it was recorded.
    /// Throws rather than reporting through `message`, which the player sheet covers.
    func saveClipToPhotos(_ clip: PlayableClip) async throws {
        let status = await PHPhotoLibrary.requestAuthorization(for: .addOnly)
        guard status == .authorized || status == .limited else { throw PhotosAccessDenied() }
        try await PHPhotoLibrary.shared().performChanges {
            let request = PHAssetCreationRequest.forAsset()
            request.addResource(with: .video, fileURL: clip.url, options: nil)
            request.creationDate = clip.recorded
        }
    }

    /// Start of the capture window on that day, using the current window setting.
    private func timelapseStart(of day: TimeLapseDay) -> Date? {
        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.dateFormat = "yyyy-MM-dd HH:mm"
        return formatter.date(from: "\(day.date) \(timelapse?.windowStart ?? "06:00")")
    }

    func forget() {
        PairingStore.clear()
        client = nil
        status = nil
        recordings = []
        events = []
        timelapse = nil
        snapshot = nil
        token = ""
        fingerprint = ""
        message = "Pairing removed from this phone."
    }

    func refresh() async {
        await perform {
            try await load(with: try connected())
            message = "Updated just now."
        }
    }

    func takeSnapshot() async {
        await perform {
            message = "Capturing a full-resolution frame…"
            let data = try await connected().snapshot(width: 0)
            guard let image = UIImage(data: data) else { throw ControllerError.invalidImage }
            let folder = FileManager.default.temporaryDirectory.appendingPathComponent("solstead-snapshots", isDirectory: true)
            try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
            let stamp = Date().formatted(.iso8601.year().month().day().time(includingFractionalSeconds: false).dateTimeSeparator(.standard))
                .replacingOccurrences(of: ":", with: "-")
            let url = folder.appendingPathComponent("Solstead \(stamp).jpg")
            try data.write(to: url, options: .atomic)
            snapshot = Snapshot(image: image, data: data, url: url, taken: Date())
            try await load(with: try connected())
            message = "Snapshot: \(Int(image.size.width))×\(Int(image.size.height)), \(ByteCountFormatter.string(fromByteCount: Int64(data.count), countStyle: .file))."
        }
    }

    func dismissSnapshot() {
        if let url = snapshot?.url { try? FileManager.default.removeItem(at: url) }
        snapshot = nil
    }

    /// Adds the full-resolution snapshot to the Photos library (add-only permission).
    func saveSnapshotToPhotos() async {
        guard let snapshot else { return }
        await perform {
            let status = await PHPhotoLibrary.requestAuthorization(for: .addOnly)
            guard status == .authorized || status == .limited else {
                throw ControllerError.controller("Allow Solstead to add to Photos in Settings to save snapshots.", status: 0)
            }
            try await PHPhotoLibrary.shared().performChanges {
                let request = PHAssetCreationRequest.forAsset()
                request.addResource(with: .photo, data: snapshot.data, options: nil)
                request.creationDate = snapshot.taken
            }
            message = "Snapshot saved to Photos."
        }
    }

    func recordTestClip() async {
        await perform {
            message = "Recording a 10-second test clip…"
            try await connected().record()
            try await load(with: try connected())
            message = "Test clip saved on the controller SSD."
        }
    }

    // MARK: - Internals

    func connected() throws -> ControllerClient {
        guard let client else { throw ControllerError.controller("Connect first.", status: 0) }
        return client
    }

    func load(with client: ControllerClient) async throws {
        let newStatus = try await client.status()
        // A missing SSD must still be visible on the health screen, so don't fail on listings.
        let clips = newStatus.storage.ready ? try await client.recordings() : []
        let eventClips = newStatus.storage.ready && newStatus.events?.enabled == true ? try await client.events() : []
        let lapse = newStatus.storage.ready && newStatus.camera.configured ? try? await client.timelapse() : nil
        status = newStatus
        recordings = clips
        events = eventClips
        timelapse = lapse
    }

    func perform(_ work: () async throws -> Void, onFailure: () -> Void = {}) async {
        busy = true
        defer { busy = false }
        do {
            try await work()
        } catch {
            onFailure()
            message = error.localizedDescription
        }
    }
}

/// Prefills the pairing form from SOLSTEAD_URL, SOLSTEAD_TOKEN and SOLSTEAD_FINGERPRINT so the
/// simulator can be launched against a bench controller without typing. Debug builds only.
enum BenchPairing {
    static func fromEnvironment(_ environment: [String: String] = ProcessInfo.processInfo.environment) -> (url: String, token: String, fingerprint: String)? {
        #if DEBUG
        guard let url = environment["SOLSTEAD_URL"], let token = environment["SOLSTEAD_TOKEN"],
              let fingerprint = environment["SOLSTEAD_FINGERPRINT"] else { return nil }
        return (url, token, fingerprint)
        #else
        return nil
        #endif
    }
}

struct PlayableClip: Identifiable {
    let title: String
    let url: URL
    let recorded: Date?
    var id: String { url.absoluteString }

    /// Renames the downloaded file to `exportName`.mp4, the name shown when it is shared.
    init(title: String, downloaded: URL, exportName: String, recorded: Date?) throws {
        let url = downloaded.deletingLastPathComponent().appendingPathComponent(exportName + ".mp4")
        if url != downloaded {
            try? FileManager.default.removeItem(at: url)
            try FileManager.default.moveItem(at: downloaded, to: url)
        }
        self.title = title
        self.url = url
        self.recorded = recorded
    }

    static let fileStamp: DateFormatter = {
        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.dateFormat = "yyyy-MM-dd HH.mm"
        return formatter
    }()
}

struct PhotosAccessDenied: LocalizedError {
    var errorDescription: String? { "Allow Solstead to add to Photos in Settings to save clips." }
}

struct LiveSession: Identifiable {
    let quality: LiveQuality
    let info: LiveInfo
    let proxy: LiveProxy
    let requestedAt: Date
    var id: String { "live" }  // one sheet; quality changes update it in place
}

struct Snapshot {
    let image: UIImage
    let data: Data
    let url: URL
    let taken: Date
}
