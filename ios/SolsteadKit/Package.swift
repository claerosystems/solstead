// swift-tools-version: 5.9
import PackageDescription

let package = Package(
    name: "SolsteadKit",
    platforms: [.iOS(.v17), .macOS(.v14)],
    products: [
        .library(name: "SolsteadKit", targets: ["SolsteadKit"]),
    ],
    targets: [
        .target(name: "SolsteadKit"),
        .testTarget(name: "SolsteadKitTests", dependencies: ["SolsteadKit"]),
    ]
)
