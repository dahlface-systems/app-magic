// swift-tools-version:5.9
import PackageDescription

let package = Package(
    name: "AppMagic",
    platforms: [.macOS(.v13)],
    targets: [
        .executableTarget(name: "AppMagic", path: "Sources/AppMagic")
    ]
)
