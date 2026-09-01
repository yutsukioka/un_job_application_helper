import 'dart:io';

Future<Process> startAtlasVaultDartHelper(
  String script,
  List<String> arguments,
) {
  final flutterRoot = Platform.environment['FLUTTER_ROOT'];
  final hasFlutterRoot = flutterRoot != null && flutterRoot.isNotEmpty;
  final dartExecutable = hasFlutterRoot
      ? <String>[
          flutterRoot,
          'bin',
          'cache',
          'dart-sdk',
          'bin',
          Platform.isWindows ? 'dart.exe' : 'dart',
        ].join(Platform.pathSeparator)
      : 'dart';

  return Process.start(
    dartExecutable,
    <String>['run', script, ...arguments],
    workingDirectory: Directory.current.path,
    runInShell: Platform.isWindows && !hasFlutterRoot,
  );
}
