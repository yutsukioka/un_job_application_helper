// swift-tools-version: 6.0

import PackageDescription

let package = Package(
    name: "AtlasApple",
    defaultLocalization: "en",
    platforms: [
        .iOS(.v18),
        .macOS(.v15),
    ],
    products: [
        .library(name: "AtlasUI", targets: ["AtlasUI"]),
    ],
    targets: [
        .target(name: "AtlasUI"),
        .testTarget(name: "AtlasUITests", dependencies: ["AtlasUI"]),
    ]
)
