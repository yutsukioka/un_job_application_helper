import Foundation

public enum AtlasExternalURLPolicy {
    private static let allowedSchemes: Set<String> = ["http", "https", "mailto"]

    public static func isAllowed(_ url: URL) -> Bool {
        guard let scheme = url.scheme?.lowercased() else {
            return false
        }
        return allowedSchemes.contains(scheme)
    }

    public static func safeURL(_ url: URL?) -> URL? {
        guard let url, isAllowed(url) else {
            return nil
        }
        return url
    }
}
