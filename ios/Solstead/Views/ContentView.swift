import AVKit
import SwiftUI
import SolsteadKit

struct ContentView: View {
    @EnvironmentObject private var model: ControllerModel

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    Text(model.message).font(.callout)
                    if model.busy { ProgressView() }
                    if !model.isConnected {
                        Button("Open settings to connect", systemImage: "gearshape") { model.showSettings = true }
                    }
                }
                if let status = model.status {
                    HealthSection(status: status)
                    CameraSection(status: status)
                    if let lapse = model.timelapse {
                        TimeLapseSection(lapse: lapse)
                    }
                    RecordingsSection()
                    if let events = status.events, events.enabled {
                        EventsSection(events: events)
                    }
                }
            }
            .disabled(model.busy)
            .sheet(isPresented: $model.showSettings) { SettingsView() }
            .sheet(isPresented: Binding(get: { model.healthEntries != nil }, set: { if !$0 { model.healthEntries = nil } })) {
                HealthLogView(entries: model.healthEntries ?? [])
            }
            .sheet(isPresented: $model.showTelemetry) { TelemetryView() }
            .sheet(item: $model.playingClip) { clip in
                ClipPlayerView(clip: clip)
            }
            .sheet(item: $model.liveSession, onDismiss: { model.stopLive() }) { session in
                LiveView(session: session)
            }
            .navigationTitle("Solstead")
            .toolbar {
                ToolbarItem(placement: .topBarLeading) {
                    Image("Mark")
                        .resizable().scaledToFit()
                        .frame(height: 26)
                        .accessibilityHidden(true)
                }
                ToolbarItemGroup(placement: .topBarTrailing) {
                    if model.isConnected {
                        Button("Refresh", systemImage: "arrow.clockwise") { Task { await model.refresh() } }
                    }
                    Button("Settings", systemImage: "gearshape") { model.showSettings = true }
                }
            }
            .modifier(TitleWithUplink(network: model.isConnected ? model.status?.network : nil))
        }
        .overlay {
            if let activity = model.activity {
                WaitCard(activity: activity)
                    .transition(.opacity)
            }
        }
        .animation(.easeInOut(duration: 0.2), value: model.activity != nil)
    }
}

/// Puts the controller's uplink icon beside the large "Solstead" title (iOS 26), or in the
/// top bar on older systems.
struct TitleWithUplink: ViewModifier {
    let network: NetworkStatus?

    func body(content: Content) -> some View {
        if let network {
            if #available(iOS 26, *) {
                content.toolbar {
                    ToolbarItem(placement: .largeTitle) {
                        HStack(alignment: .firstTextBaseline, spacing: 10) {
                            Text("Solstead").font(.largeTitle.bold())
                            UplinkIcon(network: network).font(.title3)
                            Spacer()
                        }
                    }
                }
            } else {
                content.toolbar {
                    ToolbarItem(placement: .principal) { UplinkIcon(network: network) }
                }
            }
        } else {
            content
        }
    }
}

/// The controller's uplink drawn with its signal: Wi-Fi arcs or cellular bars.
struct UplinkIcon: View {
    let network: NetworkStatus

    var body: some View {
        let percent = network.uplinkSignalPercent
        Image(systemName: network.symbolName, variableValue: percent.map { Double($0) / 100 })
            .foregroundStyle(colour(percent))
            .accessibilityLabel(accessibilityText(percent))
    }

    private func colour(_ percent: Int?) -> Color {
        if network.uplink == nil { return .red }
        if let percent, percent < 30 { return .orange }
        return .secondary
    }

    private func accessibilityText(_ percent: Int?) -> String {
        guard network.uplink != nil else { return "Controller has no internet connection" }
        let signal = percent.map { ", \(NetworkStatus.strength($0).lowercased()) signal" } ?? ""
        return "Controller on \(network.uplinkName)\(signal)"
    }
}

/// Covers the screen while a camera request runs, wherever the owner has scrolled.
struct WaitCard: View {
    @EnvironmentObject private var model: ControllerModel
    let activity: CameraActivity

    var body: some View {
        ZStack {
            Color.black.opacity(0.3).ignoresSafeArea()
            VStack(spacing: 14) {
                content
            }
            .padding(24)
            .frame(maxWidth: 300)
            .background(.regularMaterial, in: RoundedRectangle(cornerRadius: 16))
            .accessibilityElement(children: .contain)
            .accessibilityAddTraits(.isModal)
        }
    }

    @ViewBuilder private var content: some View {
        switch activity.phase {
        case let .working(step, countdown):
            TimelineView(.periodic(from: activity.started, by: 0.25)) { context in
                VStack(spacing: 14) {
                    if let countdown, countdown.fraction(at: context.date) < 1 {
                        ProgressView(value: countdown.fraction(at: context.date))
                        Text(step).multilineTextAlignment(.center)
                    } else {
                        ProgressView().controlSize(.large)
                        Text(countdown?.then ?? step).multilineTextAlignment(.center)
                    }
                    Text("\(Int(context.date.timeIntervalSince(activity.started))) s")
                        .font(.caption).monospacedDigit().foregroundStyle(.secondary)
                        .accessibilityLabel("\(Int(context.date.timeIntervalSince(activity.started))) seconds elapsed")
                }
            }
            Button("Cancel", role: .cancel) { model.cancelActivity() }
                .buttonStyle(.bordered)
        case let .succeeded(text):
            Image(systemName: "checkmark.circle.fill")
                .font(.system(size: 40)).foregroundStyle(.green)
                .accessibilityHidden(true)
            Text(text).multilineTextAlignment(.center)
        case let .failed(error):
            Image(systemName: "exclamationmark.triangle.fill")
                .font(.system(size: 36)).foregroundStyle(.red)
                .accessibilityHidden(true)
            Text(error).foregroundStyle(.red).multilineTextAlignment(.center)
            Button("OK") { model.dismissActivity() }
                .buttonStyle(.borderedProminent)
        }
    }
}

struct SettingsView: View {
    @EnvironmentObject private var model: ControllerModel
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            Form {
                PairingSection()
                if let encryption = model.encryption {
                    EncryptionSection(encryption: encryption)
                }
                if let lapse = model.timelapse {
                    TimeLapseSettingsSection(lapse: lapse)
                }
                if let charger = model.charger {
                    ChargerSettingsSection(charger: charger)
                }
                if let network = model.network, network.supportsPreference {
                    NetworkSettingsSection(network: network)
                }
                if let telemetry = model.telemetry {
                    TelemetrySettingsSection(telemetry: telemetry)
                }
                Section("About") {
                    HStack(spacing: 14) {
                        Image("Mark").resizable().scaledToFit().frame(height: 44).accessibilityHidden(true)
                        VStack(alignment: .leading) {
                            Text("Solstead").font(.headline)
                            Text("Your home under the sun. Your footage stays yours.").font(.caption).foregroundStyle(.secondary)
                        }
                    }
                    LabeledContent("App", value: Bundle.main.infoDictionary?["CFBundleShortVersionString"] as? String ?? "")
                    if let status = model.status {
                        LabeledContent("Controller software", value: status.version)
                    }
                }
            }
            .navigationTitle("Settings")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar { Button("Done") { dismiss() } }
            .disabled(model.busy)
            .sheet(isPresented: Binding(get: { model.recoverySetup != nil }, set: { if !$0 { model.cancelEncryptionSetup() } })) {
                if let key = model.recoverySetup { RecoveryKeySetupView(key: key) }
            }
            .sheet(isPresented: $model.showRestore) { RestoreAccessView() }
        }
    }
}

struct TimeLapseSettingsSection: View {
    @EnvironmentObject private var model: ControllerModel
    let lapse: TimeLapseStatus
    static let intervals = [60, 120, 300, 600, 900, 1800, 3600]

    var body: some View {
        Section {
            Toggle("Capture time-lapse", isOn: Binding(
                get: { lapse.enabled },
                set: { on in Task { await model.updateTimelapse(TimeLapseSettingsUpdate(enabled: on)) } }
            ))
            Picker("Every", selection: Binding(
                get: { lapse.intervalSeconds },
                set: { seconds in Task { await model.updateTimelapse(TimeLapseSettingsUpdate(intervalSeconds: seconds)) } }
            )) {
                ForEach(Self.intervals, id: \.self) { seconds in
                    Text(seconds < 3600 ? "\(seconds / 60) min" : "1 hour").tag(seconds)
                }
                if !Self.intervals.contains(lapse.intervalSeconds) {
                    Text("\(lapse.intervalSeconds) s").tag(lapse.intervalSeconds)
                }
            }
            DatePicker("From", selection: windowBinding(start: true), displayedComponents: .hourAndMinute)
            DatePicker("Until", selection: windowBinding(start: false), displayedComponents: .hourAndMinute)
            LabeledContent("Frames per day", value: "\(lapse.framesPerDay)")
            if let days = lapse.estimatedRetentionDays {
                LabeledContent("Estimated history", value: "about \(days) days")
            }
        } header: {
            Text("Time-lapse")
        } footer: {
            Text("Frames use their own \(ByteCountFormatter.string(fromByteCount: lapse.allocationBytes, countStyle: .file)) allocation on the controller; the oldest are overwritten when it fills. Changing the interval changes how many days fit.")
        }
    }

    private func windowBinding(start: Bool) -> Binding<Date> {
        Binding(
            get: { Self.date(from: start ? lapse.windowStart : lapse.windowEnd) },
            set: { date in
                let text = Self.text(from: date)
                let window = start ? "\(text)-\(lapse.windowEnd)" : "\(lapse.windowStart)-\(text)"
                Task { await model.updateTimelapse(TimeLapseSettingsUpdate(window: window)) }
            }
        )
    }

    static func date(from hhmm: String) -> Date {
        let parts = hhmm.split(separator: ":").compactMap { Int($0) }
        return Calendar.current.date(bySettingHour: parts.first ?? 0, minute: parts.count > 1 ? parts[1] : 0, second: 0, of: Date()) ?? Date()
    }

    static func text(from date: Date) -> String {
        let parts = Calendar.current.dateComponents([.hour, .minute], from: date)
        return String(format: "%02d:%02d", parts.hour ?? 0, parts.minute ?? 0)
    }
}

struct TimeLapseSection: View {
    @EnvironmentObject private var model: ControllerModel
    let lapse: TimeLapseStatus
    @State private var expanded = false

    var body: some View {
        Section {
            LabeledContent("Stored", value: "\(lapse.frames) frames over \(lapse.dayCount) days, \(ByteCountFormatter.string(fromByteCount: lapse.usedBytes, countStyle: .file))")
            if let error = lapse.lastError {
                Text(error).foregroundStyle(.red).font(.callout)
            }
            if let days = lapse.days, !days.isEmpty {
                DisclosureGroup(isExpanded: $expanded) {
                    ForEach(days) { day in
                        Button {
                            Task { await model.playTimelapse(day) }
                        } label: {
                            HStack {
                                VStack(alignment: .leading, spacing: 2) {
                                    Text(day.date)
                                    Text("\(day.frames) frames, \(ByteCountFormatter.string(fromByteCount: day.bytes, countStyle: .file))")
                                        .font(.caption).foregroundStyle(.secondary)
                                }
                                Spacer()
                                if day.isEncrypted {
                                    Image(systemName: "lock.fill").font(.caption).foregroundStyle(.secondary)
                                        .accessibilityLabel("Encrypted")
                                }
                                Image(systemName: "play.circle")
                            }
                        }
                        .foregroundStyle(.primary)
                    }
                } label: {
                    Text("Days (\(days.count))")
                }
            }
        } header: {
            Text("Time-lapse videos")
        } footer: {
            Text("Tap a day to watch it as a video at \(lapse.exportFps) frames per second. Encrypted days are decrypted and put together on this phone. Adjust the schedule in Settings.")
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
            if !model.message.isEmpty {
                Text(model.message).font(.callout).foregroundStyle(.secondary)
            }
        } header: {
            Text("Your controller")
        } footer: {
            Text("The token and fingerprint were printed when the controller was provisioned. They stay in this phone's Keychain.")
        }
    }
}

/// The controller's uplink and the signal of each radio it has.
struct NetworkRows: View {
    let network: NetworkStatus

    var body: some View {
        LabeledContent("Connection") {
            Label { Text(network.uplinkName) } icon: { UplinkIcon(network: network) }
                .labelStyle(.titleAndIcon)
        }
        .foregroundStyle(network.uplink == nil ? .red : .primary)
        if let note = network.fallbackNote {
            Label(note, systemImage: "arrow.triangle.branch").foregroundStyle(.orange)
        }
        if let preferred = network.preferred {
            LabeledContent("Preferred", value: preferred.name)
        }
        if let wifi = network.wifi {
            LabeledContent(network.uplink == .wifi ? "Wi-Fi signal" : "Wi-Fi signal (standby)",
                           value: NetworkStatus.describe(percent: wifi.signalPercent, dbm: wifi.signalDbm))
        }
        if let cell = network.cellular {
            LabeledContent(network.uplink == .cellular ? "Cellular signal" : "Cellular signal (standby)",
                           value: NetworkStatus.describe(percent: cell.signalPercent)
                               + (cell.operator.map { ", \($0)" } ?? ""))
        }
    }
}

/// Settings: which link the controller prefers, with the one it is using now.
struct NetworkSettingsSection: View {
    @EnvironmentObject private var model: ControllerModel
    let network: NetworkStatus

    var body: some View {
        Section {
            Picker("Prefer", selection: Binding(
                get: { network.preferred ?? .wifi },
                set: { choice in
                    guard choice != network.preferred else { return }
                    Task { await model.setPreferredUplink(choice) }
                }
            )) {
                ForEach(NetworkStatus.Preference.allCases, id: \.self) { preference in
                    Text(preference.name).tag(preference)
                }
            }
            .pickerStyle(.segmented)
            LabeledContent("Using now") {
                Label { Text(network.uplinkName) } icon: { UplinkIcon(network: network) }
                    .labelStyle(.titleAndIcon)
            }
            .foregroundStyle(network.uplink == nil ? .red : .primary)
            if let note = network.fallbackNote {
                Label(note, systemImage: "arrow.triangle.branch").foregroundStyle(.orange)
            }
        } header: {
            Text("Internet connection")
        } footer: {
            Text(Self.footer(for: network.preferred ?? .wifi))
        }
    }

    static func footer(for preference: NetworkStatus.Preference) -> String {
        let fallback = "If it has no internet, the controller switches to the other link and comes back once it works again."
        switch preference {
        case .wifi: return "Uses Wi-Fi. " + fallback
        case .cellular: return "Uses cellular, which counts against your data plan. " + fallback
        }
    }
}

struct HealthSection: View {
    @EnvironmentObject private var model: ControllerModel
    let status: ControllerStatus
    @State private var expanded = false

    enum Condition {
        case good, warning, danger

        var colour: Color {
            switch self {
            case .good: return .green
            case .warning: return .yellow
            case .danger: return .red
            }
        }

        var label: String {
            switch self {
            case .good: return "All good"
            case .warning: return "Needs attention"
            case .danger: return "Problem"
            }
        }
    }

    /// Red for anything that stops recording or corrupts evidence, yellow for degraded or
    /// recently troubled, green otherwise.
    static func condition(of status: ControllerStatus) -> Condition {
        let health = status.health
        if !status.storage.ready || health?.clockOk == false || (health?.lastDay.error ?? 0) > 0 {
            return .danger
        }
        if let events = status.events, events.enabled, !(events.running && events.bufferRunning && events.cameraSubscribed) {
            return .danger
        }
        if status.thermal?.level == "danger" { return .danger }
        if (health?.lastDay.warning ?? 0) > 0 || status.events?.cameraError != nil || status.events?.lastError != nil
            || status.thermal?.level == "warning" {
            return .warning
        }
        return .good
    }

    var body: some View {
        let condition = Self.condition(of: status)
        Section {
            DisclosureGroup(isExpanded: $expanded) {
                LabeledContent("Name", value: status.name)
                LabeledContent("Software", value: status.version)
                LabeledContent("Uptime", value: Duration.seconds(status.uptimeSeconds).formatted(.units(allowed: [.days, .hours, .minutes], width: .abbreviated)))
                if let temperature = status.temperatureC {
                    LabeledContent("CPU temperature", value: String(format: "%.1f °C", temperature) + thermalNote)
                        .foregroundStyle(thermalColour)
                }
                if let thermal = status.thermal, thermal.flagsAvailable {
                    LabeledContent("Power supply", value: thermal.undervoltageNow ? "Under-voltage now"
                        : thermal.undervoltageSinceBoot ? "Under-voltage since boot" : "OK")
                        .foregroundStyle(thermal.undervoltageNow ? .red : thermal.undervoltageSinceBoot ? .orange : .primary)
                    if thermal.throttledNow || thermal.throttledSinceBoot {
                        LabeledContent("CPU throttling", value: thermal.throttledNow ? "Now" : "Happened since boot")
                            .foregroundStyle(thermal.throttledNow ? .red : .orange)
                    }
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
                if let network = status.network {
                    NetworkRows(network: network)
                }
                LabeledContent("Remote access", value: status.remoteAccess ? "On" : "Off (local network only)")
                if let health = status.health {
                    if !health.clockOk {
                        Label("Controller clock is not set", systemImage: "clock.badge.exclamationmark").foregroundStyle(.red)
                    }
                    LabeledContent("Last 24 hours", value: health.lastDay.error == 0 && health.lastDay.warning == 0
                        ? "No problems" : "\(health.lastDay.error) errors, \(health.lastDay.warning) warnings")
                    ForEach(health.latest) { entry in
                        HealthRow(entry: entry)
                    }
                    Button("Show health log", systemImage: "list.bullet.rectangle") { Task { await model.showHealth() } }
                }
                if status.telemetry != nil {
                    Button("Power & temperature history", systemImage: "chart.xyaxis.line") { model.showTelemetry = true }
                }
            } label: {
                HStack(spacing: 10) {
                    Circle()
                        .fill(condition.colour)
                        .frame(width: 12, height: 12)
                        .accessibilityLabel(condition.label)
                    Text("Controller health")
                    Spacer()
                    Text(condition.label).foregroundStyle(.secondary)
                }
            }
        }
    }

    private var thermalNote: String {
        guard let thermal = status.thermal, let temperature = thermal.temperatureC else { return "" }
        if temperature >= thermal.throttleC { return " (throttling)" }
        if temperature >= thermal.warningC { return String(format: " (throttles at %.0f °C)", thermal.throttleC) }
        return ""
    }

    private var thermalColour: Color {
        switch status.thermal?.level {
        case "danger": return .red
        case "warning": return .orange
        default: return .primary
        }
    }

    private func bytes(_ value: Int64) -> String {
        ByteCountFormatter.string(fromByteCount: value, countStyle: .file)
    }
}

struct HealthRow: View {
    let entry: HealthEntry

    var body: some View {
        HStack(alignment: .top, spacing: 8) {
            Image(systemName: icon).foregroundStyle(colour)
            VStack(alignment: .leading, spacing: 2) {
                Text(entry.message)
                Text(entry.date, format: .dateTime.month().day().hour().minute().second())
                    .font(.caption).foregroundStyle(.secondary)
            }
        }
    }

    private var icon: String {
        switch entry.level {
        case "error": return "xmark.octagon.fill"
        case "warning": return "exclamationmark.triangle.fill"
        default: return "checkmark.circle"
        }
    }

    private var colour: Color {
        switch entry.level {
        case "error": return .red
        case "warning": return .orange
        default: return .secondary
        }
    }
}

struct HealthLogView: View {
    @Environment(\.dismiss) private var dismiss
    let entries: [HealthEntry]

    var body: some View {
        NavigationStack {
            List {
                if entries.isEmpty { Text("Nothing logged yet").foregroundStyle(.secondary) }
                ForEach(entries) { entry in HealthRow(entry: entry) }
            }
            .navigationTitle("Health log")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar { Button("Done") { dismiss() } }
        }
    }
}

struct CameraSection: View {
    @EnvironmentObject private var model: ControllerModel
    let status: ControllerStatus

    var body: some View {
        Section("Camera") {
            if let snapshot = model.snapshot {
                VStack(alignment: .leading, spacing: 8) {
                    Image(uiImage: snapshot.image)
                        .resizable().scaledToFit()
                        .clipShape(RoundedRectangle(cornerRadius: 8))
                        .accessibilityLabel("Latest requested camera snapshot")
                        .overlay(alignment: .topTrailing) {
                            Button {
                                model.dismissSnapshot()
                            } label: {
                                Image(systemName: "xmark.circle.fill")
                                    .font(.title2)
                                    .symbolRenderingMode(.palette)
                                    .foregroundStyle(.white, .black.opacity(0.6))
                                    .padding(6)
                            }
                            .buttonStyle(.plain)
                            .accessibilityLabel("Close snapshot")
                        }
                    HStack(spacing: 12) {
                        Button { Task { await model.saveSnapshotToPhotos() } } label: {
                            Label("Save to Photos", systemImage: "square.and.arrow.down").lineLimit(1).fixedSize()
                        }
                        ShareLink(item: snapshot.url, preview: SharePreview("Solstead snapshot", image: Image(uiImage: snapshot.image))) {
                            Label("Share", systemImage: "square.and.arrow.up").lineLimit(1).fixedSize()
                        }
                        Spacer()
                    }
                    .buttonStyle(.bordered)
                    .labelStyle(.titleAndIcon)
                }
                .padding(.vertical, 4)
            }
            Button("View live", systemImage: "video") { model.viewLive() }
                .disabled(!(status.live?.available ?? status.camera.configured))
            Button("Take snapshot", systemImage: "camera") { model.takeSnapshot() }
                .disabled(!status.camera.configured)
            Button("Save last 10 seconds", systemImage: "record.circle") { model.saveManualRecording() }
                .disabled(!status.camera.configured || !status.storage.ready)
            Text("Saves a manual recording from the stream buffer. If the buffer isn't running, it records the next 10 seconds instead.")
                .font(.caption).foregroundStyle(.secondary)
        }
    }
}

struct EventsSection: View {
    @EnvironmentObject private var model: ControllerModel
    let events: ControllerStatus.Events
    @State private var expanded = false
    @State private var deleting: Event?

    var body: some View {
        Section {
            LabeledContent("Detection", value: detectionText)
            if let error = events.cameraError ?? events.lastError {
                Text(error).foregroundStyle(.red).font(.callout)
            }
            LabeledContent("Stored", value: "\(events.count) events, \(bytes(events.usedBytes)) of \(bytes(events.allocationBytes))")
            DisclosureGroup(isExpanded: $expanded) {
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
                            if event.isEncrypted {
                                Image(systemName: "lock.fill").font(.caption).foregroundStyle(.secondary)
                                    .accessibilityLabel("Encrypted")
                            }
                            Image(systemName: "play.circle")
                        }
                    }
                    .foregroundStyle(.primary)
                    .swipeActions {
                        Button("Delete", systemImage: "trash", role: .destructive) { deleting = event }
                    }
                }
            } label: {
                Text(model.events.isEmpty ? "Recorded events" : "Recorded events (\(model.events.count))")
            }
        } header: {
            Text("Events")
        } footer: {
            Text("Clips start 10 seconds before detected activity and end 30 seconds after it. Oldest events are overwritten when the allocation is full. Swipe left on an event to delete it.")
        }
        .confirmationDialog("Delete this event?", isPresented: Binding(get: { deleting != nil }, set: { if !$0 { deleting = nil } }),
                            titleVisibility: .visible, presenting: deleting) { event in
            Button("Delete Event", role: .destructive) { Task { await model.delete(event) } }
        } message: { event in
            Text("The clip from \(event.started.formatted(date: .abbreviated, time: .shortened)) is removed from the controller and cannot be recovered. Copies you saved to Photos or shared are not affected.")
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
    @EnvironmentObject private var model: ControllerModel
    @Environment(\.dismiss) private var dismiss
    @Environment(\.openURL) private var openURL
    @State private var player: AVPlayer?
    @State private var saving = SaveState.idle
    @State private var saveError: Error?

    private enum SaveState { case idle, saving, saved }

    var body: some View {
        NavigationStack {
            VideoPlayer(player: player)
                .ignoresSafeArea(edges: .bottom)
                .navigationTitle(clip.title)
                .navigationBarTitleDisplayMode(.inline)
                .toolbar {
                    ToolbarItem(placement: .cancellationAction) { Button("Done") { dismiss() } }
                    ToolbarItemGroup(placement: .primaryAction) {
                        saveButton
                        ShareLink(item: clip.url, preview: SharePreview(clip.title)) {
                            Label("Share", systemImage: "square.and.arrow.up")
                        }
                    }
                }
                .alert("Couldn't save to Photos", isPresented: Binding(get: { saveError != nil }, set: { if !$0 { saveError = nil } }), presenting: saveError) { error in
                    if error is PhotosAccessDenied {
                        Button("Open Settings") {
                            if let url = URL(string: UIApplication.openSettingsURLString) { openURL(url) }
                        }
                    }
                    Button("OK", role: .cancel) {}
                } message: { error in
                    Text(error.localizedDescription)
                }
                .onAppear {
                    let player = AVPlayer(url: clip.url)
                    self.player = player
                    player.play()
                }
                .onDisappear { player?.pause() }
        }
    }

    @ViewBuilder private var saveButton: some View {
        switch saving {
        case .saving:
            ProgressView().accessibilityLabel("Saving to Photos")
        case .saved:
            Label("Saved to Photos", systemImage: "checkmark.circle")
        case .idle:
            Button { Task { await save() } } label: {
                Label("Save to Photos", systemImage: "square.and.arrow.down")
            }
        }
    }

    private func save() async {
        saving = .saving
        do {
            try await model.saveClipToPhotos(clip)
            saving = .saved
            try? await Task.sleep(for: .seconds(2))
            saving = .idle
        } catch {
            saving = .idle
            saveError = error
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
    @State private var expanded = false

    var body: some View {
        Section("Manual recordings") {
            if model.recordings.isEmpty {
                Text("No recordings yet").foregroundStyle(.secondary)
            } else {
                LabeledContent("Stored", value: "\(model.recordings.count) recordings, \(ByteCountFormatter.string(fromByteCount: model.recordings.reduce(0) { $0 + $1.sizeBytes }, countStyle: .file))")
                DisclosureGroup(isExpanded: $expanded) {
                    ForEach(model.recordings) { clip in
                        Button {
                            Task { await model.play(clip) }
                        } label: {
                            HStack {
                                VStack(alignment: .leading, spacing: 2) {
                                    Text(clip.created, format: .dateTime.weekday(.wide).month().day().hour().minute())
                                    Text(ByteCountFormatter.string(fromByteCount: clip.sizeBytes, countStyle: .file))
                                        .font(.caption).foregroundStyle(.secondary)
                                }
                                Spacer()
                                if clip.isEncrypted {
                                    Image(systemName: "lock.fill").font(.caption).foregroundStyle(.secondary)
                                        .accessibilityLabel("Encrypted")
                                }
                                Image(systemName: "play.circle")
                            }
                        }
                        .foregroundStyle(.primary)
                    }
                } label: {
                    Text("Recordings (\(model.recordings.count))")
                }
                .foregroundStyle(.primary)
            }
        }
    }
}

#Preview {
    ContentView().environmentObject(ControllerModel())
}
