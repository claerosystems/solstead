import SwiftUI
import SolsteadKit

/// Settings: the solar charger the controller reads for battery and solar history.
struct ChargerSettingsSection: View {
    @EnvironmentObject private var model: ControllerModel
    let charger: ChargerStatus
    @State private var picking = false
    @State private var confirmRemove = false

    var body: some View {
        Section {
            if charger.configured {
                LabeledContent("Charger", value: charger.name ?? charger.address ?? "")
                if let reading = charger.reading {
                    LabeledContent("Battery", value: ChargerFormat.volts(reading.batteryV))
                    LabeledContent("Solar", value: ChargerFormat.watts(reading.solarW.map(Double.init)))
                    LabeledContent("System use", value: ChargerFormat.watts(reading.loadW))
                    LabeledContent("Charging", value: reading.state.capitalized)
                }
                if charger.keyMismatch {
                    Label("The saved key no longer matches this charger. Copy it again from VictronConnect.",
                          systemImage: "key.slash").foregroundStyle(.orange)
                } else if let heard = charger.lastHeardUnix,
                          Date().timeIntervalSince1970 - TimeInterval(heard) > 300 {
                    Label("Not heard since \(Date(timeIntervalSince1970: TimeInterval(heard)).formatted(date: .omitted, time: .shortened)).",
                          systemImage: "antenna.radiowaves.left.and.right.slash").foregroundStyle(.orange)
                }
                Button("Change charger…") { picking = true }
                Button("Remove charger", role: .destructive) { confirmRemove = true }
            } else {
                Button("Connect solar charger…") { picking = true }
            }
            if let error = charger.error {
                Label(error, systemImage: "exclamationmark.triangle").foregroundStyle(.orange)
            }
        } header: {
            Text("Solar charger")
        } footer: {
            Text("The controller reads a Victron SmartSolar charger's Bluetooth broadcast once a minute for battery, solar and system-use history. No pairing or PIN is needed.")
        }
        .sheet(isPresented: $picking) { ChargerPickerView() }
        .confirmationDialog("Remove the solar charger?", isPresented: $confirmRemove, titleVisibility: .visible) {
            Button("Remove", role: .destructive) { Task { await model.forgetCharger() } }
        } message: {
            Text("The controller stops reading it and deletes its key. History already saved is kept.")
        }
    }
}

/// Picks a charger the controller can hear and takes its Instant Readout key.
struct ChargerPickerView: View {
    @EnvironmentObject private var model: ControllerModel
    @Environment(\.dismiss) private var dismiss
    @State private var seen: [SeenCharger] = []
    @State private var selected: String?
    @State private var key = ""
    @State private var error: String?
    @State private var saving = false
    @State private var scanError: String?

    private var keyIsValid: Bool {
        let trimmed = key.trimmingCharacters(in: .whitespacesAndNewlines)
        return trimmed.count == 32 && trimmed.allSatisfy(\.isHexDigit)
    }

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    let chargers = seen.filter(\.solarCharger)
                    if chargers.isEmpty {
                        HStack(spacing: 10) {
                            ProgressView()
                            Text("Listening for Victron chargers near the controller…").foregroundStyle(.secondary)
                        }
                    }
                    ForEach(chargers) { charger in
                        Button {
                            selected = charger.address
                        } label: {
                            HStack {
                                VStack(alignment: .leading) {
                                    Text(charger.name).foregroundStyle(.primary)
                                    Text(charger.address).font(.caption.monospaced()).foregroundStyle(.secondary)
                                }
                                Spacer()
                                if let rssi = charger.rssi {
                                    Text("\(rssi) dBm").font(.caption).foregroundStyle(.secondary)
                                }
                                if selected == charger.address {
                                    Image(systemName: "checkmark").foregroundStyle(Color.accentColor)
                                }
                            }
                        }
                    }
                    if let scanError { Text(scanError).foregroundStyle(.orange) }
                } header: {
                    Text("Chargers the controller can hear")
                } footer: {
                    Text("The charger must be powered and within Bluetooth range of the controller, usually a few metres.")
                }
                Section {
                    TextField("32 characters", text: $key, axis: .vertical)
                        .font(.body.monospaced())
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                    if let error { Text(error).foregroundStyle(.red) }
                } header: {
                    Text("Instant Readout key")
                } footer: {
                    Text("In VictronConnect, open the charger, tap the settings gear, then ⋮ › Product info. Turn on Instant readout via Bluetooth, tap Show next to its encryption data and copy the advertisement key. It stays on the controller.")
                }
            }
            .navigationTitle("Solar charger")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("Cancel") { dismiss() } }
                ToolbarItem(placement: .confirmationAction) {
                    if saving { ProgressView() } else {
                        Button("Connect") { Task { await save() } }
                            .disabled(selected == nil || !keyIsValid)
                    }
                }
            }
            .task { await listen() }
        }
    }

    /// Refreshes the list every few seconds while the sheet is open.
    private func listen() async {
        while !Task.isCancelled {
            do {
                let status = try await model.connected().charger()
                seen = status.seen ?? []
                scanError = status.error
                if selected == nil {
                    selected = seen.first(where: \.chosen)?.address
                        ?? (seen.filter(\.solarCharger).count == 1 ? seen.first(where: \.solarCharger)?.address : nil)
                }
            } catch {
                scanError = error.localizedDescription
            }
            try? await Task.sleep(for: .seconds(3))
        }
    }

    private func save() async {
        guard let selected else { return }
        saving = true
        defer { saving = false }
        do {
            try await model.chooseCharger(address: selected,
                                          key: key.trimmingCharacters(in: .whitespacesAndNewlines))
            dismiss()
        } catch {
            self.error = error.localizedDescription
        }
    }
}

enum ChargerFormat {
    static func volts(_ value: Double?) -> String {
        value.map { String(format: "%.2f V", $0) } ?? "–"
    }

    static func watts(_ value: Double?) -> String {
        value.map { String(format: $0 < 10 ? "%.1f W" : "%.0f W", $0) } ?? "–"
    }
}
