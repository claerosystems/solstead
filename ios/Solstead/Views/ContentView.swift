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
                    CameraSection(status: status)
                    RecordingsSection()
                }
            }
            .disabled(model.busy)
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
            Button("Take snapshot", systemImage: "camera") { Task { await model.takeSnapshot() } }
                .disabled(!status.camera.configured)
            Button("Record 10-second test clip", systemImage: "record.circle") { Task { await model.recordTestClip() } }
                .disabled(!status.camera.configured || !status.storage.ready)
            Text("Manual bench recording. Automatic event recording and live video are coming next.")
                .font(.caption).foregroundStyle(.secondary)
        }
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
