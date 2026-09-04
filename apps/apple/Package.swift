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
        .executable(name: "AtlasMacHost", targets: ["AtlasMacHost"]),
    ],
    targets: [
        .target(name: "AtlasUI"),
        // AtlasMacAppProcessOwner is the production lifecycle boundary.
        .executableTarget(name: "AtlasMacHost", dependencies: ["AtlasUI"]),
        .testTarget(name: "AtlasUITests", dependencies: ["AtlasUI"]),
    ]
)
