import SolsteadKit
import SwiftUI
import UniformTypeIdentifiers
import VisionKit

/// Restores this phone's access to encrypted recordings with the owner's recovery key: typed,
/// opened from the saved file, or scanned from the printout's QR code. The key lives only in
/// this view's state and is gone when it closes.
struct RestoreAccessView: View {
    @EnvironmentObject private var model: ControllerModel
    @Environment(\.dismiss) private var dismiss
    @State private var text = ""
    @State private var importing = false
    @State private var scanning = false
    @State private var attempted = false

    private var parsed: Result<AgeIdentity, Error>? {
        let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return nil }
        return Result { try RecoveryKey.parse(trimmed) }
    }

    private var matches: Bool {
        if case .success(let identity) = parsed { return identity.recipient == model.recoveryPublicKey }
        return false
    }

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    Text("Enter the recovery key you printed or saved when you turned on encryption. This phone uses it once to unlock your recordings and does not keep it.")
                }
                Section {
                    TextField("AGE-SECRET-KEY-1…", text: $text, axis: .vertical)
                        .lineLimit(3...6)
                        .font(.system(.body, design: .monospaced))
                        .textInputAutocapitalization(.characters).autocorrectionDisabled()
                    Button("Open key file", systemImage: "doc") { importing = true }
                    if DataScannerViewController.isSupported {
                        Button("Scan QR code", systemImage: "qrcode.viewfinder") { scanning = true }
                    }
                } header: {
                    Text("Recovery key")
                } footer: {
                    switch parsed {
                    case nil: Text("Spaces and line breaks are ignored; the AGE-SECRET-KEY-1 prefix is optional.")
                    case .failure(let error): Text(error.localizedDescription).foregroundStyle(.red)
                    case .success where !matches: Text("That is a valid key, but not this controller's recovery key.").foregroundStyle(.red)
                    case .success: Label("This is the recovery key for this controller.", systemImage: "checkmark.seal")
                    }
                }
                Section {
                    Button("Restore access") {
                        guard case .success(let identity) = parsed else { return }
                        attempted = true
                        Task {
                            if await model.restoreAccess(with: identity) {
                                text = ""
                                dismiss()
                            }
                        }
                    }
                    .disabled(!matches || model.busy)
                } footer: {
                    if model.busy || attempted {
                        Text(model.message)
                    }
                }
            }
            .navigationTitle("Restore access")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("Cancel") { dismiss() }.disabled(model.busy) }
            }
            .interactiveDismissDisabled(model.busy)
            .fileImporter(isPresented: $importing, allowedContentTypes: [.plainText, .text, .data]) { result in
                guard case .success(let url) = result else { return }
                let scoped = url.startAccessingSecurityScopedResource()
                defer { if scoped { url.stopAccessingSecurityScopedResource() } }
                if let contents = try? String(contentsOf: url, encoding: .utf8) { text = contents }
            }
            .sheet(isPresented: $scanning) {
                QRScannerView { value in
                    text = value
                    scanning = false
                }
                .ignoresSafeArea()
            }
        }
    }
}

/// Live camera scanning for a QR code; calls back with the first code's text.
struct QRScannerView: UIViewControllerRepresentable {
    let found: (String) -> Void

    func makeUIViewController(context: Context) -> DataScannerViewController {
        let scanner = DataScannerViewController(recognizedDataTypes: [.barcode(symbologies: [.qr])],
                                                isHighlightingEnabled: true)
        scanner.delegate = context.coordinator
        try? scanner.startScanning()
        return scanner
    }

    func updateUIViewController(_ controller: DataScannerViewController, context: Context) {}

    func makeCoordinator() -> Coordinator { Coordinator(found: found) }

    final class Coordinator: NSObject, DataScannerViewControllerDelegate {
        let found: (String) -> Void
        init(found: @escaping (String) -> Void) { self.found = found }

        func dataScanner(_ scanner: DataScannerViewController, didAdd items: [RecognizedItem], allItems: [RecognizedItem]) {
            for case .barcode(let code) in items {
                if let value = code.payloadStringValue {
                    scanner.stopScanning()
                    found(value)
                    return
                }
            }
        }
    }
}
