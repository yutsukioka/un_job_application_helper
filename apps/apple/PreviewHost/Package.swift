// swift-tools-version: 6.0

import PackageDescription

let package = Package(
    name: "AtlasApplePreviewHost",
    defaultLocalization: "en",
    platforms: [
        .macOS(.v15),
    ],
    dependencies: [
        .package(path: ".."),
    ],
    targets: [
        .executableTarget(
            name: "AtlasPreviewApp",
            dependencies: [
                .product(name: "AtlasUI", package: "apple"),
            ]
        ),
        .executableTarget(
            name: "AtlasIconExport",
            dependencies: [
                .product(name: "AtlasUI", package: "apple"),
            ]
        ),
    ]
)
