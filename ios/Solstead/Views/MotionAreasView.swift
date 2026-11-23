import SwiftUI
import SolsteadKit

/// Draw the areas of the camera picture where motion counts, and choose shadow filtering.
/// Motion outside every area, such as passing traffic, no longer starts a recording.
struct MotionAreasView: View {
    @EnvironmentObject private var model: ControllerModel
    @Environment(\.dismiss) private var dismiss

    @State private var saved: MotionSettings?
    @State private var settings = MotionSettings()
    @State private var picture: UIImage?
    @State private var selected: MotionArea.ID?
    @State private var loading = true
    @State private var saving = false
    @State private var error: String?

    private var changed: Bool {
        guard let saved else { return false }
        return !settings.sameChoices(as: saved)
    }

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    editor
                    HStack {
                        Button("Add area", systemImage: "plus.square.dashed") { addArea() }
                            .disabled(settings.zones.count >= MotionArea.maximumAreas || picture == nil)
                        Spacer()
                        if let index = selectedIndex {
                            Button("Delete", systemImage: "trash", role: .destructive) {
                                settings.zones.remove(at: index)
                                selected = settings.zones.last?.id
                            }
                        }
                    }
                    .buttonStyle(.bordered)
                    .labelStyle(.titleAndIcon)
                } footer: {
                    Text(settings.zones.isEmpty
                         ? "No areas: motion anywhere in the picture counts. Add an area to watch only part of it, such as your driveway but not the road."
                         : "Motion counts only inside the shaded areas. Drag a corner to reshape, drag inside an area to move it, and tap a + to add a corner. Tap a corner twice to remove it.")
                }

                if let index = selectedIndex {
                    Section("Selected area") {
                        TextField("Name", text: $settings.zones[index].name)
                    }
                }

                Section {
                    Toggle("Ignore shadows", isOn: $settings.ignoreShadows)
                    Picker("Sensitivity", selection: $settings.sensitivity) {
                        Text("Low").tag(MotionSensitivity.low)
                        Text("Medium").tag(MotionSensitivity.medium)
                        Text("High").tag(MotionSensitivity.high)
                    }
                    .pickerStyle(.segmented)
                } header: {
                    Text("Filtering")
                } footer: {
                    Text("Ignore shadows skips patches that only got darker while keeping their texture, like a cloud or a tree's shadow, and sudden changes across the whole picture. Low sensitivity needs larger, longer movement; high also catches small or distant movement.")
                }

                Section {
                    Toggle("Check motion on the controller", isOn: $settings.verify)
                    if let check = saved?.check {
                        LabeledContent("Recorded", value: "\(check.confirmed)")
                        LabeledContent("Ignored", value: "\(check.ignored)")
                        if check.passedThrough > 0 {
                            LabeledContent("Recorded unchecked", value: "\(check.passedThrough)")
                        }
                        if let lastError = check.lastError {
                            Text(lastError).font(.callout).foregroundStyle(.orange)
                        }
                    }
                } header: {
                    Text("Since the controller started")
                } footer: {
                    Text("The camera notices movement; the controller then checks it against your areas and filters. If checking ever stops working, the controller records everything the camera reports, so nothing is missed.")
                }

                if let error {
                    Section { Text(error).foregroundStyle(.red) }
                }
            }
            .navigationTitle("Motion areas")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("Cancel") { dismiss() } }
                ToolbarItem(placement: .confirmationAction) {
                    if saving {
                        ProgressView()
                    } else {
                        Button("Save") { Task { await save() } }.disabled(!changed)
                    }
                }
            }
            .interactiveDismissDisabled(changed)
            .task { await load() }
        }
    }

    private var selectedIndex: Int? {
        settings.zones.firstIndex { $0.id == selected }
    }

    @ViewBuilder private var editor: some View {
        if let picture {
            Image(uiImage: picture)
                .resizable()
                .scaledToFit()
                .overlay { AreaCanvas(zones: $settings.zones, selected: $selected) }
                .clipShape(RoundedRectangle(cornerRadius: 8))
                .accessibilityLabel("Camera picture with motion areas")
        } else if loading {
            HStack { Spacer(); ProgressView("Getting a picture…"); Spacer() }
                .frame(minHeight: 180)
        } else {
            Button("Try again", systemImage: "arrow.clockwise") { Task { await load() } }
        }
    }

    private func addArea() {
        let area = MotionArea.rectangle(named: "Area \(settings.zones.count + 1)")
        settings.zones.append(area)
        selected = area.id
    }

    private func load() async {
        loading = true
        error = nil
        defer { loading = false }
        do {
            let client = try model.connected()
            async let current = client.motion()
            async let snapshot = client.snapshot(width: 960)
            let loaded = try await current
            saved = loaded
            settings = loaded
            selected = loaded.zones.first?.id
            guard let image = UIImage(data: try await snapshot) else { throw ControllerError.invalidImage }
            picture = image
        } catch {
            self.error = error.localizedDescription
        }
    }

    private func save() async {
        saving = true
        error = nil
        defer { saving = false }
        do {
            let updated = try await model.connected().updateMotion(settings)
            saved = updated
            model.message = settings.zones.isEmpty
                ? "Motion settings saved. The whole picture is watched."
                : "Motion areas saved on the controller."
            dismiss()
        } catch {
            self.error = error.localizedDescription
        }
    }
}

/// The areas drawn over the picture, with handles to reshape the selected one.
private struct AreaCanvas: View {
    @Binding var zones: [MotionArea]
    @Binding var selected: MotionArea.ID?
    @State private var dragStart: [MotionPoint]?
    @State private var armedCorner: Int?

    var body: some View {
        GeometryReader { geometry in
            let size = geometry.size
            ZStack {
                ForEach(zones) { area in
                    let isSelected = area.id == selected
                    AreaShape(points: area.points)
                        .fill(Color.yellow.opacity(isSelected ? 0.3 : 0.18))
                        .overlay(AreaShape(points: area.points)
                            .stroke(isSelected ? Color.yellow : Color.white.opacity(0.8),
                                    style: StrokeStyle(lineWidth: isSelected ? 2.5 : 1.5, lineJoin: .round)))
                        .contentShape(AreaShape(points: area.points))
                        .onTapGesture { selected = area.id; armedCorner = nil }
                        .gesture(moveGesture(area.id, size: size))
                }
                if let index = zones.firstIndex(where: { $0.id == selected }) {
                    handles(index, size: size)
                }
            }
            .frame(width: size.width, height: size.height)
        }
    }

    @ViewBuilder private func handles(_ index: Int, size: CGSize) -> some View {
        let area = zones[index]
        ForEach(area.points.indices, id: \.self) { corner in
            Circle()
                .fill(armedCorner == corner ? Color.red : Color.yellow)
                .overlay(Circle().stroke(.black.opacity(0.5), lineWidth: 1))
                .frame(width: 18, height: 18)
                .frame(width: 44, height: 44)  // comfortable touch target
                .contentShape(Rectangle())
                .position(x: area.points[corner].x * size.width, y: area.points[corner].y * size.height)
                .gesture(cornerGesture(index, corner: corner, size: size))
                .onTapGesture { tapCorner(index, corner: corner) }
                .accessibilityLabel("Corner \(corner + 1) of \(area.name)")
        }
        if area.points.count < MotionArea.maximumPoints {
            ForEach(area.points.indices, id: \.self) { edge in
                let middle = area.midpoint(after: edge)
                Image(systemName: "plus.circle.fill")
                    .font(.system(size: 16))
                    .symbolRenderingMode(.palette)
                    .foregroundStyle(.black, .white.opacity(0.85))
                    .frame(width: 36, height: 36)
                    .contentShape(Rectangle())
                    .position(x: middle.x * size.width, y: middle.y * size.height)
                    .onTapGesture {
                        zones[index].points.insert(middle, at: edge + 1)
                        armedCorner = nil
                    }
                    .accessibilityLabel("Add a corner")
            }
        }
    }

    private func tapCorner(_ index: Int, corner: Int) {
        // First tap arms the corner (shown red); a second tap removes it.
        if armedCorner == corner, zones[index].points.count > MotionArea.minimumPoints {
            zones[index].points.remove(at: corner)
            armedCorner = nil
        } else {
            armedCorner = corner
        }
    }

    private func cornerGesture(_ index: Int, corner: Int, size: CGSize) -> some Gesture {
        DragGesture(minimumDistance: 2)
            .onChanged { drag in
                guard zones.indices.contains(index), zones[index].points.indices.contains(corner) else { return }
                armedCorner = nil
                zones[index].points[corner] = MotionPoint(x: drag.location.x / size.width,
                                                          y: drag.location.y / size.height).clamped
            }
    }

    private func moveGesture(_ id: MotionArea.ID, size: CGSize) -> some Gesture {
        DragGesture(minimumDistance: 4)
            .onChanged { drag in
                guard let index = zones.firstIndex(where: { $0.id == id }) else { return }
                if dragStart == nil {
                    dragStart = zones[index].points
                    selected = id
                    armedCorner = nil
                }
                guard let start = dragStart else { return }
                zones[index] = zones[index].moved(from: start, dx: drag.translation.width / size.width,
                                                  dy: drag.translation.height / size.height)
            }
            .onEnded { _ in dragStart = nil }
    }
}

private struct AreaShape: Shape {
    let points: [MotionPoint]

    func path(in rect: CGRect) -> Path {
        var path = Path()
        guard let first = points.first else { return path }
        path.move(to: CGPoint(x: first.x * rect.width, y: first.y * rect.height))
        for point in points.dropFirst() {
            path.addLine(to: CGPoint(x: point.x * rect.width, y: point.y * rect.height))
        }
        path.closeSubpath()
        return path
    }
}
