import CoreImage.CIFilterBuiltins
import SolsteadKit
import SwiftUI
import UniformTypeIdentifiers

/// The owner's switch for encrypting recordings on the controller (ADR 0003).
struct EncryptionSection: View {
    @EnvironmentObject private var model: ControllerModel
    let encryption: EncryptionStatus
    @State private var confirmingOff = false

    var body: some View {
        Section {
            Toggle("Encrypt recordings", isOn: Binding(
                get: { encryption.enrolled },
                set: { on in
                    if on { model.beginEncryptionSetup() } else { confirmingOff = true }
                }))
            if encryption.enrolled {
                LabeledContent("This phone", value: model.thisPhoneEnrolled ? "Can open recordings" : "Not enrolled")
                if encryption.deviceKeys.count > 1 {
                    LabeledContent("Phones with access", value: "\(encryption.deviceKeys.count)")
                }
                if let waiting = encryption.unencryptedEvents, waiting > 0 {
                    LabeledContent("Older clips to encrypt", value: "\(waiting)")
                }
                if let waiting = encryption.unencryptedFrames, waiting > 0 {
                    LabeledContent("Older time-lapse frames to encrypt", value: "\(waiting)")
                }
                Button(model.thisPhoneEnrolled ? "Unlock older recordings with recovery key" : "Restore access with recovery key",
                       systemImage: "key") { model.showRestore = true }
            }
        } header: {
            Text("Encryption")
        } footer: {
            Text(encryption.enrolled
                 ? "Recordings are encrypted on the controller. Only enrolled phones and your recovery key can open them; the controller itself cannot, so a stolen controller or drive reveals nothing."
                 : "When on, recordings are encrypted on the controller so only your phone or your recovery key can open them. Setup asks you to print or save a recovery key.")
        }
        .confirmationDialog("Turn off encryption?", isPresented: $confirmingOff, titleVisibility: .visible) {
            Button("Turn Off", role: .destructive) { Task { await model.disableEncryption() } }
        } message: {
            Text("New recordings will be stored unencrypted, so anyone who takes the controller or its drive can watch them. Recordings already encrypted stay encrypted and still play on this phone.")
        }
    }
}

/// Shows a new recovery key once, offers printing and saving it, asks for two groups back, then
/// enrols. Nothing here stores the recovery key.
struct RecoveryKeySetupView: View {
    @EnvironmentObject private var model: ControllerModel
    let key: RecoveryKey
    @State private var step = Step.show
    @State private var challenge: [Int] = []
    @State private var answers = ["", ""]
    @State private var exporting = false
    @State private var saved = false
    @State private var printed = false
    @State private var attempted = false  // show the model's message only after enrolling fails

    enum Step { case show, confirm }

    private var controllerName: String { model.status?.name ?? "Solstead" }

    var body: some View {
        NavigationStack {
            Form {
                switch step {
                case .show: showStep
                case .confirm: confirmStep
                }
            }
            .navigationTitle("Recovery key")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("Cancel") { model.cancelEncryptionSetup() } }
            }
            .interactiveDismissDisabled()
            .disabled(model.busy)
            .fileExporter(isPresented: $exporting, document: RecoveryKeyFile(text: key.fileContents(controllerName: controllerName)),
                          contentType: .plainText, defaultFilename: "Solstead recovery key - \(controllerName).txt") { result in
                if case .success = result { saved = true }
            }
        }
    }

    @ViewBuilder private var showStep: some View {
        Section {
            Text("This key opens every encrypted recording if your phone is lost. Solstead cannot recover it for you, and this phone will not keep it.")
            Text("Print it or save it as a file somewhere other than this phone, such as a password manager or a USB drive. Doing both is safest.")
                .foregroundStyle(.secondary)
        }
        Section("Your recovery key") {
            RecoveryCodeGrid(key: key)
            HStack {
                Spacer()
                QRCodeImage(text: key.code).frame(width: 160, height: 160)
                Spacer()
            }
        }
        Section {
            Button(printed ? "Print again" : "Print", systemImage: "printer") { printSheet() }
            Button(saved ? "Save file again" : "Save as file", systemImage: "square.and.arrow.down") { exporting = true }
        } footer: {
            if saved || printed {
                Text([saved ? "File saved." : nil, printed ? "Sent to the printer." : nil].compactMap { $0 }.joined(separator: " "))
            }
        }
        Section {
            Button("I have saved my recovery key") {
                challenge = RecoveryKey.challenge(groupCount: key.groups.count)
                answers = ["", ""]
                step = .confirm
            }
        }
    }

    @ViewBuilder private var confirmStep: some View {
        Section {
            ForEach(0..<challenge.count, id: \.self) { slot in
                TextField("Group \(challenge[slot] + 1)", text: $answers[slot])
                    .textInputAutocapitalization(.characters).autocorrectionDisabled()
                    .font(.system(.body, design: .monospaced))
            }
        } header: {
            Text("Check your copy")
        } footer: {
            Text("From your printout or file, type groups \(challenge.map { String($0 + 1) }.joined(separator: " and ")) of the key. Groups are numbered from the left, after AGE-SECRET-KEY-1.")
        }
        Section {
            Button("Turn on encryption") {
                attempted = true
                Task { await model.finishEncryptionSetup(key) }
            }
            .disabled(!confirmed)
            Button("Show the key again") { step = .show }
        } footer: {
            if attempted && !model.busy {
                Text(model.message).foregroundStyle(.red)
            }
        }
    }

    private var confirmed: Bool {
        challenge.count == 2 && zip(challenge, answers).allSatisfy { key.matches(group: $0, typed: $1) }
    }

    private func printSheet() {
        let renderer = ImageRenderer(content: RecoveryPrintout(key: key, controllerName: controllerName))
        let page = CGRect(x: 0, y: 0, width: 612, height: 792)  // US Letter; fits A4 margins too
        let pdf = NSMutableData()
        renderer.render { size, draw in
            var box = page
            guard let consumer = CGDataConsumer(data: pdf as CFMutableData),
                  let context = CGContext(consumer: consumer, mediaBox: &box, nil) else { return }
            context.beginPDFPage(nil)
            context.translateBy(x: (page.width - size.width) / 2, y: page.height - size.height - 48)
            draw(context)
            context.endPDFPage()
            context.closePDF()
        }
        let controller = UIPrintInteractionController.shared
        let info = UIPrintInfo(dictionary: nil)
        info.jobName = "Solstead recovery key"
        info.outputType = .grayscale
        controller.printInfo = info
        controller.printingItem = pdf as Data
        controller.present(animated: true) { _, completed, _ in if completed { printed = true } }
    }
}

/// The key after its fixed prefix, numbered in groups of five.
struct RecoveryCodeGrid: View {
    let key: RecoveryKey

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(RecoveryKey.prefix).font(.system(.callout, design: .monospaced)).foregroundStyle(.secondary)
            LazyVGrid(columns: Array(repeating: GridItem(.flexible(), alignment: .leading), count: 3), alignment: .leading, spacing: 8) {
                ForEach(Array(key.groups.enumerated()), id: \.offset) { index, group in
                    HStack(alignment: .firstTextBaseline, spacing: 4) {
                        Text("\(index + 1)").font(.caption2).foregroundStyle(.secondary).frame(minWidth: 14, alignment: .trailing)
                        Text(group).font(.system(.body, design: .monospaced).weight(.semibold))
                    }
                }
            }
        }
        .textSelection(.enabled)
        .accessibilityElement(children: .combine)
    }
}

/// The printed page: code, QR code, what it is for, and how to use it without Solstead.
struct RecoveryPrintout: View {
    let key: RecoveryKey
    let controllerName: String

    var body: some View {
        VStack(alignment: .leading, spacing: 18) {
            Text("Solstead recovery key").font(.title.bold())
            Text("\(controllerName) · created \(Date().formatted(date: .long, time: .omitted))").font(.headline)
            Text("This key opens every encrypted recording and backup from this system if your phones are lost. Keep it private and somewhere safe. Solstead cannot recover it for you.")
            RecoveryCodeGrid(key: key)
            HStack(alignment: .top, spacing: 18) {
                QRCodeImage(text: key.code).frame(width: 150, height: 150)
                VStack(alignment: .leading, spacing: 6) {
                    Text("The QR code holds the same key.").font(.caption)
                    Text("Recordings also open with the free age tool:").font(.caption).padding(.top, 6)
                    Text("age -d -i key.txt recording.mp4.age > recording.mp4").font(.system(size: 9, design: .monospaced))
                }
            }
        }
        .foregroundStyle(.black)
        .padding(8)
        .frame(width: 520, alignment: .leading)
        .background(.white)
    }
}

struct QRCodeImage: View {
    let text: String

    var body: some View {
        if let image = Self.render(text) {
            Image(uiImage: image).interpolation(.none).resizable().scaledToFit()
                .accessibilityLabel("QR code of the recovery key")
        }
    }

    static func render(_ text: String) -> UIImage? {
        let filter = CIFilter.qrCodeGenerator()
        filter.message = Data(text.utf8)
        filter.correctionLevel = "M"
        guard let output = filter.outputImage?.transformed(by: CGAffineTransform(scaleX: 8, y: 8)),
              let cgImage = CIContext().createCGImage(output, from: output.extent) else { return nil }
        return UIImage(cgImage: cgImage)
    }
}

/// The recovery key as a standard age identity file for `.fileExporter`.
struct RecoveryKeyFile: FileDocument {
    static var readableContentTypes: [UTType] { [.plainText] }
    let text: String

    init(text: String) { self.text = text }

    init(configuration: ReadConfiguration) throws {
        text = String(decoding: configuration.file.regularFileContents ?? Data(), as: UTF8.self)
    }

    func fileWrapper(configuration: WriteConfiguration) throws -> FileWrapper {
        FileWrapper(regularFileWithContents: Data(text.utf8))
    }
}
