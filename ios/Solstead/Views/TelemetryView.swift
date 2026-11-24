import Charts
import SwiftUI
import SolsteadKit

/// Power & temperature: the controller's telemetry as charts on one shared time axis.
struct TelemetryView: View {
    @EnvironmentObject private var model: ControllerModel
    @Environment(\.dismiss) private var dismiss
    @State private var range: TelemetryRange = .day
    @State private var history: TelemetryHistory?
    @State private var error: String?
    @State private var loading = false

    var body: some View {
        NavigationStack {
            List {
                Section {
                    Picker("Range", selection: $range) {
                        ForEach(TelemetryRange.allCases) { Text($0.label).tag($0) }
                    }
                    .pickerStyle(.segmented)
                    if let status = model.telemetry, !status.clockSynchronized {
                        Label("The controller's clock is not set yet. Readings are held and saved once it is.",
                              systemImage: "clock.badge.exclamationmark")
                            .foregroundStyle(.orange)
                    }
                    if let error { Text(error).foregroundStyle(.red) }
                    if loading && history == nil { ProgressView() }
                }
                if let history, history.points.contains(where: { $0.batteryV != nil }) {
                    BatterySection(history: history)
                    PowerSection(history: history)
                } else {
                    Section("Battery and solar") {
                        Text(model.charger?.configured == true
                             ? "No solar charger readings in this range yet. The controller saves one sample a minute."
                             : "Connect a solar charger in Settings to see battery voltage, solar input and system use.")
                            .foregroundStyle(.secondary)
                    }
                }
                if let history {
                    TemperatureSection(history: history, thermal: model.status?.thermal)
                    SupplySection(history: history)
                    if let energy = history.energy, !energy.isEmpty {
                        EnergySection(history: history, days: energy)
                    }
                }
            }
            .navigationTitle("Power & temperature")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar { Button("Done") { dismiss() } }
            .task(id: range) { await load() }
            .refreshable { await load() }
        }
    }

    private func load() async {
        loading = true
        defer { loading = false }
        do {
            history = try await model.connected().telemetry(range: range)
            error = nil
        } catch {
            self.error = error.localizedDescription
        }
    }
}

private struct TemperatureSection: View {
    let history: TelemetryHistory
    let thermal: ControllerStatus.Thermal?

    var body: some View {
        let runs = history.segments { $0.cpuC }
        Section {
            if runs.isEmpty {
                Text("No temperature readings in this range yet. The controller saves one sample a minute.")
                    .foregroundStyle(.secondary)
            } else {
                Chart {
                    ForEach(Array(runs.enumerated()), id: \.offset) { index, run in
                        ForEach(run) { point in band(point, run: index) }
                        ForEach(run) { point in line(point, run: index) }
                        if run.count == 1, let point = run.first {
                            PointMark(x: .value("Time", point.date), y: .value("Processor", point.cpuC ?? 0))
                                .foregroundStyle(Color.orange)
                                .symbolSize(20)
                        }
                    }
                    if let thermal {
                        limit(thermal.warningC, label: "Warning", colour: .yellow, labelAbove: false)
                        limit(thermal.throttleC, label: "Throttles", colour: .red, labelAbove: true)
                    }
                }
                .timeAxis(history)
                .chartYScale(domain: .automatic(includesZero: false))
                .chartYAxisLabel("°C")
                .frame(height: 220)
                .accessibilityLabel("Processor temperature over the selected range")
            }
        } header: {
            Text("Temperature")
        } footer: {
            if !runs.isEmpty {
                Text("Processor temperature. The line is the average of each \(Self.duration(history.bucketSeconds)); the band shows its coolest and hottest minute. Gaps are times the controller was off.")
            }
        }
    }

    @ChartContentBuilder
    private func band(_ point: TelemetryPoint, run: Int) -> some ChartContent {
        let average = point.cpuC ?? 0
        AreaMark(x: .value("Time", point.date),
                 yStart: .value("Lowest", point.cpuCLow ?? average),
                 yEnd: .value("Highest", point.cpuCHigh ?? average),
                 series: .value("Run", "range \(run)"))
            .foregroundStyle(Color.orange.opacity(0.2))
    }

    @ChartContentBuilder
    private func line(_ point: TelemetryPoint, run: Int) -> some ChartContent {
        LineMark(x: .value("Time", point.date),
                 y: .value("Processor", point.cpuC ?? 0),
                 series: .value("Run", "average \(run)"))
            .foregroundStyle(Color.orange)
    }

    @ChartContentBuilder
    private func limit(_ celsius: Double, label: String, colour: Color, labelAbove: Bool) -> some ChartContent {
        RuleMark(y: .value(label, celsius))
            .lineStyle(StrokeStyle(lineWidth: 1, dash: [4, 3]))
            .foregroundStyle(colour)
            .annotation(position: labelAbove ? .top : .bottom, alignment: .leading) {
                Text("\(label) \(Int(celsius)) °C").font(.caption2).foregroundStyle(.secondary)
            }
    }

    static func duration(_ seconds: Int) -> String {
        switch seconds {
        case ..<3600: return "\(seconds / 60) minutes"
        case 3600: return "hour"
        case ..<86400: return "\(seconds / 3600) hours"
        default: return "day"
        }
    }
}

/// Battery voltage: the average of each bucket, with a band down to the lowest single reading,
/// which catches the sags that brown out the controller.
private struct BatterySection: View {
    let history: TelemetryHistory
    static let lowVolts = 12.0  // the controller logs "Battery low" below this

    var body: some View {
        let runs = history.segments { $0.batteryV }
        Section {
            Chart {
                ForEach(Array(runs.enumerated()), id: \.offset) { index, run in
                    ForEach(run) { point in
                        let average = point.batteryV ?? 0
                        AreaMark(x: .value("Time", point.date),
                                 yStart: .value("Lowest", point.batteryVMin ?? average),
                                 yEnd: .value("Highest", point.batteryVHigh ?? average),
                                 series: .value("Run", "range \(index)"))
                            .foregroundStyle(Color.green.opacity(0.2))
                    }
                    ForEach(run) { point in
                        LineMark(x: .value("Time", point.date), y: .value("Battery", point.batteryV ?? 0),
                                 series: .value("Run", "average \(index)"))
                            .foregroundStyle(Color.green)
                    }
                }
                RuleMark(y: .value("Low", Self.lowVolts))
                    .lineStyle(StrokeStyle(lineWidth: 1, dash: [4, 3]))
                    .foregroundStyle(Color.red)
                    .annotation(position: .bottom, alignment: .leading) {
                        Text("Low \(String(format: "%.1f", Self.lowVolts)) V").font(.caption2).foregroundStyle(.secondary)
                    }
            }
            .timeAxis(history)
            .chartYScale(domain: .automatic(includesZero: false))
            .chartYAxisLabel("V")
            .frame(height: 200)
            .accessibilityLabel("Battery voltage over the selected range")
            if let last = history.points.last(where: { $0.batteryV != nil }) {
                LabeledContent("Latest", value: ChargerFormat.volts(last.batteryV))
                if let state = last.chargeState {
                    LabeledContent("Charging", value: state.capitalized)
                }
            }
        } header: {
            Text("Battery")
        } footer: {
            Text("The line is the average of each \(TemperatureSection.duration(history.bucketSeconds)); the band reaches down to the lowest single reading.")
        }
    }
}

/// Solar input and the system's use from the charger's load output, in watts.
private struct PowerSection: View {
    let history: TelemetryHistory

    var body: some View {
        Section {
            Chart {
                solarMarks(history.segments { $0.solarW })
                useMarks(history.segments { $0.loadW })
            }
            .chartForegroundStyleScale(["Solar": Color.orange, "System use": Color.blue])
            .timeAxis(history)
            .chartYAxisLabel("W")
            .frame(height: 200)
            .accessibilityLabel("Solar input and system use over the selected range")
        } header: {
            Text("Power")
        } footer: {
            Text("Solar is what the charger takes from the panel. System use is the camera and controller on the charger's load output.")
        }
    }

    // Split out of `body`: as one expression the chart was too much for the type checker.
    @ChartContentBuilder
    private func solarMarks(_ runs: [[TelemetryPoint]]) -> some ChartContent {
        ForEach(Array(runs.enumerated()), id: \.offset) { index, run in
            ForEach(run) { point in
                AreaMark(x: .value("Time", point.date), y: .value("Watts", point.solarW ?? 0),
                         series: .value("Run", "solar \(index)"))
                    .foregroundStyle(Color.orange.opacity(0.25))
                LineMark(x: .value("Time", point.date), y: .value("Watts", point.solarW ?? 0),
                         series: .value("Run", "solar line \(index)"))
                    .foregroundStyle(by: .value("Series", "Solar"))
            }
        }
    }

    @ChartContentBuilder
    private func useMarks(_ runs: [[TelemetryPoint]]) -> some ChartContent {
        ForEach(Array(runs.enumerated()), id: \.offset) { index, run in
            ForEach(run) { point in
                LineMark(x: .value("Time", point.date), y: .value("Watts", point.loadW ?? 0),
                         series: .value("Run", "use \(index)"))
                    .foregroundStyle(by: .value("Series", "System use"))
            }
        }
    }
}

/// Watt-hours harvested and used per day, the numbers that say whether the panel keeps up.
private struct EnergySection: View {
    let history: TelemetryHistory
    let days: [DailyEnergy]

    var body: some View {
        let solar = days.compactMap(\.solarWh).reduce(0, +)
        let used = days.compactMap(\.loadWh).reduce(0, +)
        Section {
            Chart {
                ForEach(days) { day in
                    if let date = day.day {
                        BarMark(x: .value("Day", date, unit: .day), y: .value("Wh", day.solarWh ?? 0))
                            .foregroundStyle(by: .value("Series", "Harvested"))
                            .position(by: .value("Series", "Harvested"))
                        BarMark(x: .value("Day", date, unit: .day), y: .value("Wh", day.loadWh ?? 0))
                            .foregroundStyle(by: .value("Series", "Used"))
                            .position(by: .value("Series", "Used"))
                    }
                }
            }
            .chartForegroundStyleScale(["Harvested": Color.orange, "Used": Color.blue])
            .chartYAxisLabel("Wh")
            .frame(height: 180)
            .accessibilityLabel("Energy harvested and used per day")
            LabeledContent("Harvested", value: "\(Int(solar.rounded())) Wh")
            LabeledContent("Used", value: "\(Int(used.rounded())) Wh")
        } header: {
            Text("Daily energy")
        } footer: {
            Text("Per local day. Minutes without a charger reading count as nothing, so days with gaps read low.")
        }
    }
}

/// When the Pi's supply sagged or it slowed itself down, as marks along the same time axis.
private struct SupplySection: View {
    let history: TelemetryHistory

    var body: some View {
        let reported = history.points.contains { $0.undervoltage != nil || $0.throttled != nil }
        let troubled = history.points.filter { ($0.undervoltage ?? 0) > 0 || ($0.throttled ?? 0) > 0 }
        if reported {
            Section("Supply") {
                if troubled.isEmpty {
                    Label("No under-voltage or throttling in this range", systemImage: "checkmark.circle")
                        .foregroundStyle(.secondary)
                } else {
                    Chart {
                        ForEach(troubled.filter { ($0.undervoltage ?? 0) > 0 }) { mark($0, "Under-voltage", .red) }
                        ForEach(troubled.filter { ($0.throttled ?? 0) > 0 }) { mark($0, "Throttled", .orange) }
                    }
                    .timeAxis(history)
                    .frame(height: 90)
                    let sagged = troubled.reduce(0) { $0 + ($1.undervoltage ?? 0) }
                    let slowed = troubled.reduce(0) { $0 + ($1.throttled ?? 0) }
                    LabeledContent("Under-voltage", value: "\(sagged) min")
                    LabeledContent("Throttled", value: "\(slowed) min")
                }
            }
        }
    }
}

extension SupplySection {
    @ChartContentBuilder
    fileprivate func mark(_ point: TelemetryPoint, _ flag: String, _ colour: Color) -> some ChartContent {
        RectangleMark(xStart: .value("From", point.date),
                      xEnd: .value("To", point.date.addingTimeInterval(TimeInterval(history.bucketSeconds))),
                      y: .value("Flag", flag))
            .foregroundStyle(colour)
    }
}

extension View {
    /// The shared time axis: the whole range, labelled every few hours, days or months to suit it.
    fileprivate func timeAxis(_ history: TelemetryHistory) -> some View {
        let days = Double(history.untilUnix - history.sinceUnix) / 86400
        let (unit, count, format): (Calendar.Component, Int, Date.FormatStyle) =
            days <= 2 ? (.hour, 6, .dateTime.hour())
            : days <= 8 ? (.day, 1, .dateTime.weekday(.abbreviated))
            : days <= 31 ? (.day, 7, .dateTime.month(.abbreviated).day())
            : days <= 92 ? (.month, 1, .dateTime.month(.abbreviated))
            : (.month, 2, .dateTime.month(.abbreviated))
        return chartXScale(domain: history.since...history.until)
            .chartXAxis {
                AxisMarks(values: .stride(by: unit, count: count)) { _ in
                    AxisGridLine()
                    AxisTick()
                    AxisValueLabel(format: format)
                }
            }
    }
}

/// Settings: how long the controller keeps telemetry.
struct TelemetrySettingsSection: View {
    @EnvironmentObject private var model: ControllerModel
    let telemetry: TelemetryStatus
    @State private var shorter: Int?

    var body: some View {
        Section {
            Picker("Keep history for", selection: Binding(
                get: { telemetry.retentionDays },
                set: { days in
                    if days < telemetry.retentionDays {
                        shorter = days  // deletes older days, so ask first
                    } else {
                        Task { await model.setTelemetryRetention(days) }
                    }
                }
            )) {
                ForEach(telemetry.retentionChoices, id: \.self) { days in
                    Text(ControllerModel.retentionLabel(days)).tag(days)
                }
            }
        } header: {
            Text("Power & temperature")
        } footer: {
            Text("The controller saves one sample a minute on its recording drive. A year takes about 40 MB.")
        }
        .confirmationDialog(
            "Keep only \(ControllerModel.retentionLabel(shorter ?? 0))?",
            isPresented: Binding(get: { shorter != nil }, set: { if !$0 { shorter = nil } }),
            titleVisibility: .visible
        ) {
            Button("Delete older history", role: .destructive) {
                if let days = shorter { Task { await model.setTelemetryRetention(days) } }
                shorter = nil
            }
        } message: {
            Text("History older than \(ControllerModel.retentionLabel(shorter ?? 0)) is deleted from the controller now and cannot be recovered.")
        }
    }
}
