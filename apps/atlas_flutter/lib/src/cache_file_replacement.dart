import 'dart:io';

const _replacementIntentPrefix = 'atlas-cache-replace-v1:';

File cacheReplacementTemporaryFile(File targetFile) {
  return File('${targetFile.path}.tmp');
}

File cacheReplacementPreviousFile(File targetFile) {
  return File('${targetFile.path}.previous');
}

File cacheReplacementIntentFile(File targetFile) {
  return File('${targetFile.path}.replace-intent');
}

Future<bool> hasCacheReplacementArtifacts(File targetFile) async {
  return await cacheReplacementTemporaryFile(targetFile).exists() ||
      await cacheReplacementPreviousFile(targetFile).exists() ||
      await cacheReplacementIntentFile(targetFile).exists();
}

/// Recovers a replacement interrupted after its staged file was flushed.
///
/// The intent marker is created only after the staged file is complete. An
/// unmarked staged file is therefore an uncommitted or partial write and must
/// not take precedence over the retained legacy cache.
Future<void> recoverInterruptedCacheReplacement(File targetFile) async {
  final stagedFile = cacheReplacementTemporaryFile(targetFile);
  final previousFile = cacheReplacementPreviousFile(targetFile);
  final intentFile = cacheReplacementIntentFile(targetFile);

  if (await targetFile.exists()) {
    await _deleteIfPresent(stagedFile);
    await _deleteIfPresent(previousFile);
    await _deleteIfPresent(intentFile);
    return;
  }

  if (await intentFile.exists()) {
    final expectedLength = await _replacementIntentLength(intentFile);
    final stagedFileIsComplete =
        expectedLength != null &&
        await stagedFile.exists() &&
        await stagedFile.length() == expectedLength;
    if (stagedFileIsComplete) {
      await stagedFile.rename(targetFile.path);
      await _deleteIfPresent(previousFile);
    } else if (await previousFile.exists()) {
      await previousFile.rename(targetFile.path);
    }
    await _deleteIfPresent(stagedFile);
    await _deleteIfPresent(intentFile);
    return;
  }

  if (await previousFile.exists()) {
    await previousFile.rename(targetFile.path);
  }
  await _deleteIfPresent(stagedFile);
}

/// Commits [stagedFile] without leaving Windows unable to recover its target.
///
/// POSIX rename already replaces atomically. Windows needs an explicit intent
/// plus previous-file fallback because Dart removes an existing destination
/// before renaming the staged file.
Future<void> replaceCacheFile({
  required File targetFile,
  required File stagedFile,
  bool? useWindowsRecoveryProtocol,
}) async {
  if (!(useWindowsRecoveryProtocol ?? Platform.isWindows)) {
    await stagedFile.rename(targetFile.path);
    return;
  }

  if (!await stagedFile.exists() || await stagedFile.length() == 0) {
    throw FileSystemException(
      'Cache replacement staging file is missing or empty.',
      stagedFile.path,
    );
  }

  final previousFile = cacheReplacementPreviousFile(targetFile);
  final intentFile = cacheReplacementIntentFile(targetFile);
  if (await previousFile.exists() || await intentFile.exists()) {
    throw FileSystemException(
      'A previous cache replacement has not been recovered.',
      targetFile.path,
    );
  }

  final stagedLength = await stagedFile.length();
  await intentFile.writeAsString(
    '$_replacementIntentPrefix$stagedLength\n',
    flush: true,
  );
  try {
    if (await targetFile.exists()) {
      await targetFile.rename(previousFile.path);
    }
    await stagedFile.rename(targetFile.path);
    await _deleteIfPresent(previousFile);
    await _deleteIfPresent(intentFile);
  } catch (_) {
    if (!await targetFile.exists() && await previousFile.exists()) {
      try {
        await previousFile.rename(targetFile.path);
      } catch (_) {
        // Keep the intent and previous file for startup recovery.
      }
    }
    rethrow;
  }
}

Future<void> deleteCacheReplacementArtifacts(File targetFile) async {
  await _deleteIfPresent(cacheReplacementTemporaryFile(targetFile));
  await _deleteIfPresent(cacheReplacementPreviousFile(targetFile));
  await _deleteIfPresent(cacheReplacementIntentFile(targetFile));
}

Future<void> _deleteIfPresent(File file) async {
  if (await file.exists()) {
    await file.delete();
  }
}

Future<int?> _replacementIntentLength(File intentFile) async {
  try {
    final value = await intentFile.readAsString();
    if (!value.startsWith(_replacementIntentPrefix) || !value.endsWith('\n')) {
      return null;
    }
    final encodedLength = value.substring(
      _replacementIntentPrefix.length,
      value.length - 1,
    );
    final length = int.tryParse(encodedLength);
    if (length == null || length <= 0) {
      return null;
    }
    return length;
  } catch (_) {
    return null;
  }
}
