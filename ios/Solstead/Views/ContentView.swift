import AVKit
import SwiftUI
import SolsteadKit

struct ContentView: View {
    @EnvironmentObject private var model: ControllerModel

    var body: some View {
        NavigationStack {
            Form {
                PairingSection()
                Section {
                    Text(model.message).font(.callout)
                    if model.busy { ProgressView() }
                }
                if let status = model.status {
                    HealthSection(status: status)
                    if let events = status.events, events.enabled {
                        EventsSection(events: events)
                    }
                    CameraSection(status: status)
                    RecordingsSection()
                }
            }
            .disabled(model.busy)
            .sheet(item: $model.playingClip) { clip in
                ClipPlayerView(clip: clip)
            }
            .sheet(item: $model.liveSession, onDismiss: { model.stopLive() }) { session in
                LiveView(session: session)
            }
            .navigationTitle("Solstead")
            .toolbar {
                if model.isConnected {
                    Button("Refresh", systemImage: "arrow.clockwise") { Task { await model.refresh() } }
                }
            }
        }
    }
}

struct PairingSection: View {
    @EnvironmentObject private var model: ControllerModel

    var body: some View {
        Section {
            TextField("HTTPS address", text: $model.url)
                .textContentType(.URL).keyboardType(.URL)
                .textInputAutocapitalization(.never).autocorrectionDisabled()
            SecureField("Owner access token", text: $model.token)
            TextField("Certificate SHA-256 fingerprint", text: $model.fingerprint)
                .textInputAutocapitalization(.never).autocorrectionDisabled()
                .font(.system(.body, design: .monospaced))
            Button("Connect securely") { Task { await model.connect() } }
            if model.isConnected {
                Button("Forget this controller", role: .destructive) { model.forget() }
            }
        } header: {
            Text("Your controller")
        } footer: {
            Text("The token and fingerprint were printed when the controller was provisioned. They stay in this phone's Keychain.")
        }
    }
}

struct HealthSection: View {
    let status: ControllerStatus

    var body: some View {
        Section("Controller health") {
            LabeledContent("Name", value: status.name)
            LabeledContent("Software", value: status.version)
            LabeledContent("Uptime", value: Duration.seconds(status.uptimeSeconds).formatted(.units(allowed: [.days, .hours, .minutes], width: .abbreviated)))
            if let temperature = status.temperatureC {
                LabeledContent("CPU temperature", value: String(format: "%.1f °C", temperature))
            }
            LabeledContent("Storage", value: status.storage.ready ? "Ready" : "Unavailable")
            if let free = status.storage.freeBytes, let total = status.storage.totalBytes {
                LabeledContent("Available", value: "\(bytes(free)) of \(bytes(total))")
            }
            if let error = status.storage.error {
                Text(error).foregroundStyle(.red)
            }
            LabeledContent("Camera", value: status.camera.configured ? "Configured" : "Not configured")
            if let last = status.camera.lastSuccessUnix {
                LabeledContent("Last capture", value: Date(timeIntervalSince1970: TimeInterval(last)).formatted(date: .abbreviated, time: .shortened))
            }
            LabeledContent("Remote access", value: status.remoteAccess ? "On" : "Off (local network only)")
        }
    }

    private func bytes(_ value: Int64) -> String {
        ByteCountFormatter.string(fromByteCount: value, countStyle: .file)
    }
}

struct CameraSection: View {
    @EnvironmentObject private var model: ControllerModel
    let status: ControllerStatus

    var body: some View {
        Section("Camera") {
            if let image = model.snapshot {
                Image(uiImage: image)
                    .resizable().scaledToFit()
                    .clipShape(RoundedRectangle(cornerRadius: 8))
                    .accessibilityLabel("Latest requested camera snapshot")
            }
            Button("View live", systemImage: "video") { Task { await model.startLive() } }
                .disabled(!(status.live?.available ?? status.camera.configured))
            Button("Take snapshot", systemImage: "camera") { Task { await model.takeSnapshot() } }
                .disabled(!status.camera.configured)
            Button("Record 10-second test clip", systemImage: "record.circle") { Task { await model.recordTestClip() } }
                .disabled(!status.camera.configured || !status.storage.ready)
            Text("Manual test recording from the buffered stream.")
                .font(.caption).foregroundStyle(.secondary)
        }
    }
}

struct EventsSection: View {
    @EnvironmentObject private var model: ControllerModel
    let events: ControllerStatus.Events

    var body: some View {
        Section {
            LabeledContent("Detection", value: detectionText)
            if let error = events.cameraError ?? events.lastError {
                Text(error).foregroundStyle(.red).font(.callout)
            }
            LabeledContent("Stored", value: "\(events.count) events, \(bytes(events.usedBytes)) of \(bytes(events.allocationBytes))")
            if model.events.isEmpty {
                Text("No events recorded yet").foregroundStyle(.secondary)
            }
            ForEach(model.events) { event in
                Button {
                    Task { await model.play(event) }
                } label: {
                    HStack {
                        VStack(alignment: .leading, spacing: 2) {
                            Text(event.started, format: .dateTime.weekday(.wide).month().day().hour().minute().second())
                            Text("\(event.activitySeconds) s of activity, \(event.durationSeconds) s clip, \(bytes(event.sizeBytes))")
                                .font(.caption).foregroundStyle(.secondary)
                        }
                        Spacer()
                        Image(systemName: "play.circle")
                    }
                }
                .foregroundStyle(.primary)
            }
        } header: {
            Text("Events")
        } footer: {
            Text("Clips start 10 seconds before detected activity and end 30 seconds after it. Oldest events are overwritten when the allocation is full.")
        }
    }

    private var detectionText: String {
        if !events.running { return events.reason ?? "Starting" }
        if events.motionActive { return "Activity now, recording" }
        if !events.bufferRunning { return "Camera stream down" }
        if !events.cameraSubscribed { return "Waiting for camera events" }
        return "Watching"
    }

    private func bytes(_ value: Int64) -> String {
        ByteCountFormatter.string(fromByteCount: value, countStyle: .file)
    }
}

struct ClipPlayerView: View {
    let clip: PlayableClip
    @Environment(\.dismiss) private var dismiss
    @State private var player: AVPlayer?

    var body: some View {
        NavigationStack {
            VideoPlayer(player: player)
                .ignoresSafeArea(edges: .bottom)
                .navigationTitle(clip.event.started.formatted(date: .abbreviated, time: .shortened))
                .navigationBarTitleDisplayMode(.inline)
                .toolbar { Button("Done") { dismiss() } }
                .onAppear {
                    let player = AVPlayer(url: clip.url)
                    self.player = player
                    player.play()
                }
                .onDisappear { player?.pause() }
        }
    }
}

struct LiveView: View {
    @EnvironmentObject private var model: ControllerModel
    @Environment(\.dismiss) private var dismiss
    let session: LiveSession
    @State private var player: AVPlayer?
    @State private var firstFrameSeconds: Double?
    @State private var observer: NSKeyValueObservation?

    var body: some View {
        NavigationStack {
            ZStack {
                VideoPlayer(player: player)
                    .ignoresSafeArea(edges: .bottom)
                if firstFrameSeconds == nil {
                    ProgressView().controlSize(.large)
                }
            }
            .navigationTitle("Live")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar { Button("Done") { dismiss() } }
            .safeAreaInset(edge: .bottom) {
                VStack(spacing: 6) {
                    Picker("Quality", selection: Binding(
                        get: { session.quality },
                        set: { quality in Task { await model.startLive(quality: quality) } }
                    )) {
                        ForEach(LiveQuality.allCases, id: \.self) { Text($0.label + " quality").tag($0) }
                    }
                    .pickerStyle(.segmented)
                    .padding(.horizontal)
                    Text(firstFrameSeconds.map { String(format: "First frame after %.1f s", $0) } ?? "Connecting…")
                        .font(.caption).foregroundStyle(.secondary)
                }
                .padding(.vertical, 8)
                .background(.bar)
            }
            .onAppear(perform: start)
            .onChange(of: session.requestedAt) { _, _ in start() }
            .onDisappear { observer = nil; player?.pause() }
        }
    }

    private func start() {
        observer = nil
        player?.pause()
        firstFrameSeconds = nil
        // A fresh URL each time: the playlist is the same but AVPlayer must not reuse old segments.
        let player = AVPlayer(url: session.proxy.playlistURL)
        self.player = player
        observer = player.observe(\.timeControlStatus, options: [.new]) { player, _ in
            if player.timeControlStatus == .playing, firstFrameSeconds == nil {
                DispatchQueue.main.async {
                    firstFrameSeconds = Date().timeIntervalSince(session.requestedAt)
                }
            }
        }
        player.play()
    }
}

struct RecordingsSection: View {
    @EnvironmentObject private var model: ControllerModel

    var body: some View {
        Section("Recent recordings") {
            if model.recordings.isEmpty {
                Text("No recordings yet").foregroundStyle(.secondary)
            }
            ForEach(model.recordings) { clip in
                VStack(alignment: .leading, spacing: 2) {
                    Text(clip.created, format: .dateTime.weekday(.wide).month().day().hour().minute())
                    Text(ByteCountFormatter.string(fromByteCount: clip.sizeBytes, countStyle: .file))
                        .font(.caption).foregroundStyle(.secondary)
                }
            }
        }
    }
}

#Preview {
    ContentView().environmentObject(ControllerModel())
}
