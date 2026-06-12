import SwiftUI

#if canImport(UIKit)
import UIKit
#elseif canImport(AppKit)
import AppKit
#endif

public enum AtlasTheme {
    /// Primary accent: desaturated UN-cyan. Slightly brighter in dark mode.
    public static let accent = adaptiveColor(
        light: (red: 0.00, green: 0.55, blue: 0.78),
        dark: (red: 0.25, green: 0.71, blue: 0.93)
    )

    /// Reserved exclusively for strategy-fit signals.
    public static let strategyOrange = adaptiveColor(
        light: (red: 0.91, green: 0.43, blue: 0.08),
        dark: (red: 1.00, green: 0.58, blue: 0.25)
    )

    public static let deadlineAmber = adaptiveColor(
        light: (red: 0.85, green: 0.55, blue: 0.08),
        dark: (red: 0.95, green: 0.69, blue: 0.30)
    )

    public static let deadlineRed = adaptiveColor(
        light: (red: 0.78, green: 0.16, blue: 0.14),
        dark: (red: 0.96, green: 0.42, blue: 0.38)
    )

    public static let success = adaptiveColor(
        light: (red: 0.13, green: 0.59, blue: 0.29),
        dark: (red: 0.35, green: 0.80, blue: 0.48)
    )

    public static let warning = deadlineAmber
    public static let danger = deadlineRed

    /// Deterministic per-source tint. Brightness/saturation are clamped per
    /// appearance so white monogram text stays legible in both modes.
    public static func sourceColor(for value: String) -> Color {
        let seed = value.unicodeScalars.reduce(UInt64(0)) { ($0 &* 31) &+ UInt64($1.value) }
        let hue = Double(seed % 360) / 360.0
        return adaptiveColor(
            light: hsb(hue: hue, saturation: 0.52, brightness: 0.58),
            dark: hsb(hue: hue, saturation: 0.46, brightness: 0.66)
        )
    }

    // MARK: - Helpers

    private static func hsb(
        hue: Double, saturation: Double, brightness: Double
    ) -> (red: Double, green: Double, blue: Double) {
        #if canImport(UIKit)
        let native = UIColor(hue: hue, saturation: saturation, brightness: brightness, alpha: 1)
        var r: CGFloat = 0, g: CGFloat = 0, b: CGFloat = 0, a: CGFloat = 0
        native.getRed(&r, green: &g, blue: &b, alpha: &a)
        return (Double(r), Double(g), Double(b))
        #else
        let native = NSColor(hue: hue, saturation: saturation, brightness: brightness, alpha: 1)
        return (Double(native.redComponent), Double(native.greenComponent), Double(native.blueComponent))
        #endif
    }

    private static func adaptiveColor(
        light: (red: Double, green: Double, blue: Double),
        dark: (red: Double, green: Double, blue: Double)
    ) -> Color {
        #if canImport(UIKit)
        return Color(UIColor { traits in
            let c = traits.userInterfaceStyle == .dark ? dark : light
            return UIColor(red: c.red, green: c.green, blue: c.blue, alpha: 1)
        })
        #else
        return Color(NSColor(name: nil) { appearance in
            let isDark = appearance.bestMatch(from: [.aqua, .darkAqua]) == .darkAqua
            let c = isDark ? dark : light
            return NSColor(srgbRed: c.red, green: c.green, blue: c.blue, alpha: 1)
        })
        #endif
    }
}

public enum ResultDensity: String, CaseIterable, Identifiable {
    case comfortable = "Comfortable"
    case compact = "Compact"

    public var id: String { rawValue }
}

public enum SortOrder: String, CaseIterable, Identifiable {
    case closingSoon = "Closing soon"
    case newestPosted = "Newest posted"
    case deadlineLatest = "Deadline latest"
    case bestFit = "Best fit"

    public var id: String { rawValue }
}

public enum DeadlineUrgency: Equatable {
    case neutral
    case soon
    case critical
    case passed
    case unknown

    var tint: Color {
        switch self {
        case .neutral:
            return .secondary
        case .soon:
            return AtlasTheme.deadlineAmber
        case .critical:
            return AtlasTheme.deadlineRed
        case .passed:
            return .secondary
        case .unknown:
            return .secondary
        }
    }

    var systemImage: String {
        switch self {
        case .soon, .critical:
            return "clock.badge.exclamationmark"
        case .passed:
            return "xmark.circle"
        case .neutral, .unknown:
            return "clock"
        }
    }
}
