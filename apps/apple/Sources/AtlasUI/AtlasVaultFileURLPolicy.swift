import Foundation

enum AtlasVaultFileURLPolicy {
    static func isSafeAbsoluteLocalFileURL(_ url: URL) -> Bool {
        let host = url.host
        guard url.isFileURL,
              url.path.hasPrefix("/"),
              host == nil || host?.isEmpty == true || host == "localhost",
              url.user == nil,
              url.password == nil,
              url.port == nil,
              url.query == nil,
              url.fragment == nil,
              url.absoluteString.range(of: "%00", options: .caseInsensitive) == nil,
              url.absoluteString.range(of: "%2f", options: .caseInsensitive) == nil
        else {
            return false
        }

        return url.pathComponents.dropFirst().allSatisfy(isSafePathComponent)
    }

    static func isSafePathComponent(_ component: String) -> Bool {
        !component.isEmpty
            && component != "."
            && component != ".."
            && !component.utf8.contains(0)
            && !component.contains("/")
    }
}
