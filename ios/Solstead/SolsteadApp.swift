import SwiftUI

@main
struct SolsteadApp: App {
    @StateObject private var model = ControllerModel()

    var body: some Scene {
        WindowGroup {
            ContentView()
                .environmentObject(model)
        }
    }
}

// The brand green is the app's accent colour (Assets.xcassets/AccentColor), so every control
// picks it up, including inside sheets.
