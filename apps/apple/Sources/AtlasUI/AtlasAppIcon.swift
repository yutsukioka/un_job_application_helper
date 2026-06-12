import SwiftUI

/// Atlas app icon, drawn in code so masters can be re-rendered at any size.
///
/// Concept: a magnifying-glass lens that is a wireframe globe (search across
/// the world's organizations), with a single strategy-orange dot marking the
/// matched duty station. Cyan is the brand/search color; orange is reserved
/// for "your fit", mirroring the in-app color semantics.
public struct AtlasAppIcon: View {
    public enum Style {
        /// Full-bleed square master (iOS asset catalogs apply the mask).
        case iOS
        /// Inset continuous-corner squircle with transparent margins and a
        /// baked drop shadow, per the macOS icon grid.
        case macOS
    }

    private let size: CGFloat
    private let style: Style

    public init(size: CGFloat, style: Style = .iOS) {
        self.size = size
        self.style = style
    }

    public var body: some View {
        switch style {
        case .iOS:
            artwork(side: size)
                .frame(width: size, height: size)
        case .macOS:
            let inner = size * 0.80
            artwork(side: inner)
                .frame(width: inner, height: inner)
                .clipShape(RoundedRectangle(cornerRadius: inner * 0.185, style: .continuous))
                .shadow(color: .black.opacity(0.35), radius: size * 0.012, y: size * 0.008)
                .frame(width: size, height: size)
        }
    }

    // MARK: - Artwork

    private func artwork(side s: CGFloat) -> some View {
        let lensCenter = CGPoint(x: s * 0.46, y: s * 0.44)
        let lensRadius = s * 0.295

        return ZStack {
            background(side: s, lensCenter: lensCenter)
            globe(side: s, center: lensCenter, radius: lensRadius)
            lensRim(side: s, center: lensCenter, radius: lensRadius)
            handle(side: s, center: lensCenter, radius: lensRadius)
            matchDot(side: s, center: lensCenter, radius: lensRadius)
        }
        .frame(width: s, height: s)
        .clipped()
    }

    private func background(side s: CGFloat, lensCenter: CGPoint) -> some View {
        ZStack {
            LinearGradient(
                colors: [
                    Color(red: 0.024, green: 0.094, blue: 0.165),
                    Color(red: 0.043, green: 0.208, blue: 0.329),
                ],
                startPoint: .topLeading,
                endPoint: .bottomTrailing
            )
            // Soft cyan halo behind the lens lifts it off the night ground.
            RadialGradient(
                colors: [
                    Color(red: 0.216, green: 0.714, blue: 0.941).opacity(0.34),
                    .clear,
                ],
                center: .init(
                    x: lensCenter.x / s,
                    y: lensCenter.y / s
                ),
                startRadius: 0,
                endRadius: s * 0.52
            )
        }
    }

    private func globe(side s: CGFloat, center: CGPoint, radius r: CGFloat) -> some View {
        let line = s * 0.0145
        let grid = Color.white.opacity(0.34)
        return ZStack {
            // Deep lens glass.
            Circle()
                .fill(
                    RadialGradient(
                        colors: [
                            Color(red: 0.071, green: 0.318, blue: 0.482),
                            Color(red: 0.035, green: 0.165, blue: 0.278),
                        ],
                        center: .init(x: 0.38, y: 0.32),
                        startRadius: 0,
                        endRadius: r * 1.45
                    )
                )
                .frame(width: r * 2, height: r * 2)

            // Meridians.
            ForEach([0.36, 0.72], id: \.self) { factor in
                Ellipse()
                    .stroke(grid, lineWidth: line)
                    .frame(width: r * 2 * factor, height: r * 2)
            }
            // Parallels (straight chords read cleanly at small sizes).
            ForEach([-0.42, 0.0, 0.42], id: \.self) { offset in
                let y = r * offset
                let chord = sqrt(max(0, r * r - y * y))
                Rectangle()
                    .fill(grid)
                    .frame(width: chord * 2, height: line)
                    .offset(y: y)
            }
        }
        .frame(width: r * 2, height: r * 2)
        .clipShape(Circle())
        .position(center)
    }

    private func lensRim(side s: CGFloat, center: CGPoint, radius r: CGFloat) -> some View {
        Circle()
            .stroke(
                LinearGradient(
                    colors: [
                        Color(red: 0.561, green: 0.867, blue: 1.0),
                        Color(red: 0.165, green: 0.671, blue: 0.910),
                    ],
                    startPoint: .topLeading,
                    endPoint: .bottomTrailing
                ),
                lineWidth: s * 0.052
            )
            .frame(width: r * 2, height: r * 2)
            .position(center)
            .shadow(color: Color(red: 0.165, green: 0.671, blue: 0.910).opacity(0.45), radius: s * 0.02)
    }

    private func handle(side s: CGFloat, center: CGPoint, radius r: CGFloat) -> some View {
        let length = s * 0.30
        let width = s * 0.092
        // 45 degrees toward the lower-right corner; tuck the start under the rim.
        let direction = CGVector(dx: cos(CGFloat.pi / 4), dy: sin(CGFloat.pi / 4))
        let startDistance = r - s * 0.015
        let midDistance = startDistance + length / 2
        let midpoint = CGPoint(
            x: center.x + direction.dx * midDistance,
            y: center.y + direction.dy * midDistance
        )
        return Capsule()
            .fill(
                LinearGradient(
                    colors: [
                        Color(red: 0.561, green: 0.867, blue: 1.0),
                        Color(red: 0.114, green: 0.553, blue: 0.788),
                    ],
                    startPoint: .top,
                    endPoint: .bottom
                )
            )
            .frame(width: length, height: width)
            .rotationEffect(.degrees(45))
            .position(midpoint)
            .shadow(color: .black.opacity(0.25), radius: s * 0.012, y: s * 0.008)
    }

    private func matchDot(side s: CGFloat, center: CGPoint, radius r: CGFloat) -> some View {
        // Sits on the intersection of the upper parallel (y = -0.42r) and the
        // right meridian (x = 0.36r ellipse edge at that latitude).
        let dotCenter = CGPoint(x: center.x + r * 0.327, y: center.y - r * 0.42)
        let orange = Color(red: 0.949, green: 0.455, blue: 0.169)
        return ZStack {
            Circle()
                .fill(orange.opacity(0.35))
                .frame(width: s * 0.135, height: s * 0.135)
                .blur(radius: s * 0.012)
            Circle()
                .fill(orange)
                .frame(width: s * 0.082, height: s * 0.082)
            Circle()
                .fill(Color.white.opacity(0.85))
                .frame(width: s * 0.026, height: s * 0.026)
                .offset(x: -s * 0.012, y: -s * 0.012)
        }
        .position(dotCenter)
    }
}

#Preview("iOS 256") {
    AtlasAppIcon(size: 256, style: .iOS)
}

#Preview("macOS 256") {
    AtlasAppIcon(size: 256, style: .macOS)
        .background(Color.gray.opacity(0.2))
}

#Preview("Small sizes") {
    HStack(spacing: 16) {
        AtlasAppIcon(size: 16)
        AtlasAppIcon(size: 32)
        AtlasAppIcon(size: 64)
        AtlasAppIcon(size: 128)
    }
    .padding()
}
