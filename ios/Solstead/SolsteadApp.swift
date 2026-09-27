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

// Brand colours live in Assets.xcassets: AccentColor is Sky (a lighter sky in dark mode) and
// SunColor is the sun from the mark. See docs/brand/README.md.
