import SwiftUI

@main
struct SolsteadApp: App {
    @StateObject private var model = ControllerModel()

    var body: some Scene {
        WindowGroup {
            ContentView()
                .environmentObject(model)
                .tint(.solsteadGreen)
        }
    }
}

extension Color {
    /// Brand green from docs/brand.
    static let solsteadGreen = Color(red: 0.16, green: 0.36, blue: 0.28)
}
