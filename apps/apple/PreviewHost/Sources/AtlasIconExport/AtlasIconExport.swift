import AppKit
import AtlasUI
import SwiftUI

/// Renders the Atlas app icon masters to PNG files.
///
/// Usage: swift run AtlasIconExport [output-directory]
/// Defaults to ../Design/AppIcon relative to the PreviewHost package.
@main
@MainActor
struct AtlasIconExport {
    static func main() {
        let arguments = CommandLine.arguments
        let outputDir: URL
        if arguments.count > 1 {
            outputDir = URL(fileURLWithPath: arguments[1], isDirectory: true)
        } else {
            outputDir = URL(fileURLWithPath: FileManager.default.currentDirectoryPath)
                .appendingPathComponent("../Design/AppIcon", isDirectory: true)
                .standardizedFileURL
        }
        do {
            try FileManager.default.createDirectory(at: outputDir, withIntermediateDirectories: true)
        } catch {
            fatalError("Could not create output directory: \(error)")
        }

        let masters: [(name: String, size: CGFloat, style: AtlasAppIcon.Style)] = [
            ("AppIcon-iOS-1024", 1024, .iOS),
            ("AppIcon-macOS-1024", 1024, .macOS),
            ("AppIcon-iOS-180", 180, .iOS),
            ("AppIcon-macOS-512", 512, .macOS),
            ("AppIcon-macOS-256", 256, .macOS),
            ("AppIcon-preview-64", 64, .iOS),
        ]

        for master in masters {
            let renderer = ImageRenderer(
                content: AtlasAppIcon(size: master.size, style: master.style)
            )
            renderer.scale = 1
            renderer.isOpaque = master.style == .iOS
            guard
                let nsImage = renderer.nsImage,
                let tiff = nsImage.tiffRepresentation,
                let rep = NSBitmapImageRep(data: tiff),
                let png = rep.representation(using: .png, properties: [:])
            else {
                print("FAILED to render \(master.name)")
                continue
            }
            let url = outputDir.appendingPathComponent("\(master.name).png")
            do {
                try png.write(to: url)
                print("Wrote \(url.path)")
            } catch {
                print("FAILED to write \(master.name): \(error)")
            }
        }
    }
}
