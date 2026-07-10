import Darwin
import Foundation

enum AtlasVaultTestFileSystemSupport {
    static func canonicalTemporaryRoot() throws -> URL {
        var resolvedPath = [CChar](repeating: 0, count: Int(PATH_MAX))
        let temporaryRoot = URL(fileURLWithPath: NSTemporaryDirectory(), isDirectory: true)
        let resolutionError = temporaryRoot.withUnsafeFileSystemRepresentation { path -> Int32 in
            guard let path else {
                return EINVAL
            }
            return resolvedPath.withUnsafeMutableBufferPointer { buffer -> Int32 in
                guard realpath(path, buffer.baseAddress) != nil else {
                    return errno
                }
                return 0
            }
        }
        guard resolutionError == 0 else {
            throw NSError(
                domain: NSPOSIXErrorDomain,
                code: Int(resolutionError),
                userInfo: [
                    NSLocalizedDescriptionKey:
                        "Unable to resolve the test temporary directory: \(String(cString: strerror(resolutionError)))"
                ]
            )
        }
        let path = String(
            decoding: resolvedPath.prefix { $0 != 0 }.map { UInt8(bitPattern: $0) },
            as: UTF8.self
        )
        return URL(fileURLWithPath: path, isDirectory: true)
    }
}
