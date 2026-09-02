part of 'sync_queue.dart';

final class AtlasVaultRevocationException implements Exception {
  const AtlasVaultRevocationException([
    this.code = 'ATLAS_REVOCATION_REJECTED',
  ]);
  final String code;
  @override
  String toString() => code;
}

Never _revFail([String code = 'ATLAS_REVOCATION_REJECTED']) =>
    throw AtlasVaultRevocationException(code);
const _revFields = [
  'account_id',
  'vault_id',
  'target_device_id',
  'initiator_device_id',
  'prior_registry_root',
  'resulting_registry_root',
  'key_epoch',
  'sequence',
  'authorization_category',
];
const _revFormat = 'atlasvault-device-revocation';

T _revBoundary<T>(T Function() body) {
  try {
    return body();
  } on AtlasVaultRevocationException {
    rethrow;
  } catch (_) {
    _revFail();
  }
}

List<Map<String, Object?>> _revEntries(Object? raw) {
  if (raw is! List || raw.isEmpty || raw.length > 256) _revFail();
  return raw.map((e) => Map<String, Object?>.from(e as Map)).toList();
}

Map<String, Object?> _revCopy(Map<String, Object?> v) =>
    Map<String, Object?>.from(jsonDecode(jsonEncode(v)) as Map);
String _revID(Object? value) => _commitmentIdentifier(value);
int _revNumber(Object? value) {
  final n = _commitmentSequence(value);
  if (n >= 9007199254740991) _revFail();
  return n;
}

Uint8List _revBytes(Object? value, int size) {
  if (value is! String || value.length != 4 * ((size + 2) ~/ 3)) _revFail();
  return _base64(value, exactLength: size);
}

abstract final class AtlasVaultRevocation {
  static String registryRoot(
    List<Map<String, Object?>> entries,
  ) => _revBoundary(() {
    if (entries.isEmpty || entries.length > 256) _revFail();
    final rows = <String, String>{};
    for (final e in entries) {
      _exact(e, {
        'device_id',
        'signing_public_b64',
        'agreement_public_b64',
        'state',
      });
      final signing = _revBytes(e['signing_public_b64'], 32),
          agreement = _revBytes(e['agreement_public_b64'], 32);
      final device =
          'avd1-${_sha256Hex([...ascii.encode('atlasvault-device-id-v1:'), ...signing, ...agreement])}';
      if (e['device_id'] != device ||
          rows.containsKey(device) ||
          !['ACTIVE', 'REVOKED'].contains(e['state'])) {
        _revFail();
      }
      String hex(List<int> b) =>
          b.map((n) => n.toRadixString(16).padLeft(2, '0')).join();
      rows[device] =
          '$device:${hex(signing)}:${hex(agreement)}:${e['state']}\n';
    }
    final ids = rows.keys.toList()..sort();
    return _sha256Hex(
      ascii.encode(
        'atlasvault-revocation-registry-v1\n${ids.map((id) => rows[id]).join()}',
      ),
    );
  });

  static String _root(Map<String, Object?> v) {
    _exact(v, {'format', 'version', ..._revFields});
    if (v['format'] != _revFormat ||
        v['version'] is! int ||
        v['version'] != 1) {
      _revFail();
    }
    for (final f in _revFields) {
      if (f == 'key_epoch' || f == 'sequence') {
        _revNumber(v[f]);
      } else if (f.endsWith('root')) {
        _commitmentHex(v[f]);
      } else {
        _revID(v[f]);
      }
    }
    if (v['authorization_category'] != 'DEVICE_PRESENCE') _revFail();
    return _sha256Hex(
      ascii.encode(
        'atlasvault-device-revocation-v1\n${_revFields.map((f) => '${v[f]}\n').join()}',
      ),
    );
  }

  static Uint8List _message(String root) => Uint8List.fromList([
    ...ascii.encode('atlasvault-revocation-signature-v1\x00'),
    for (var i = 0; i < 64; i += 2)
      int.parse(root.substring(i, i + 2), radix: 16),
  ]);
  static List<Map<String, Object?>> _removed(
    List<Map<String, Object?>> entries,
    String target,
    String initiator,
  ) {
    registryRoot(entries);
    final active = entries
        .where((e) => e['state'] == 'ACTIVE')
        .map((e) => e['device_id'])
        .toSet();
    if (!active.contains(target) ||
        !active.contains(initiator) ||
        active.difference({target}).isEmpty) {
      _revFail('ATLAS_REMOVAL_AUTHORITY');
    }
    return entries
        .map(
          (e) => {
            ...e,
            'state': e['device_id'] == target ? 'REVOKED' : e['state'],
          },
        )
        .toList();
  }

  static Future<List<Map<String, Object?>>> verify(
    Map<String, Object?> v,
    List<Map<String, Object?>> entries,
  ) async {
    try {
      _exact(v, {'format', 'version', ..._revFields, 'root', 'signature_b64'});
      final unsigned = Map<String, Object?>.of(v)
        ..remove('root')
        ..remove('signature_b64');
      final root = _root(unsigned),
          after = _removed(
            entries,
            v['target_device_id'] as String,
            v['initiator_device_id'] as String,
          );
      if (v['root'] != root ||
          v['prior_registry_root'] != registryRoot(entries) ||
          v['resulting_registry_root'] != registryRoot(after)) {
        _revFail();
      }
      final signer = entries.singleWhere(
        (e) => e['device_id'] == v['initiator_device_id'],
      );
      if (!await Ed25519().verify(
        _message(root),
        signature: Signature(
          _revBytes(v['signature_b64'], 64),
          publicKey: SimplePublicKey(
            _revBytes(signer['signing_public_b64'], 32),
            type: KeyPairType.ed25519,
          ),
        ),
      )) {
        _revFail();
      }
      return after;
    } on AtlasVaultRevocationException {
      rethrow;
    } catch (_) {
      _revFail();
    }
  }

  /// Against a previously verified removal, not an applied epoch or HPKE artifact.
  static void validateRotationPlan(
    Map<String, Object?> plan,
    Map<String, Object?> transition,
    List<Map<String, Object?>> registry,
    String stateRoot,
  ) => _revBoundary(() {
    _commitmentHex(stateRoot);
    _exact(transition, {
      'format',
      'version',
      ..._revFields,
      'root',
      'signature_b64',
    });
    final unsigned = Map<String, Object?>.of(transition)
      ..remove('root')
      ..remove('signature_b64');
    if (transition['root'] != _root(unsigned)) _revFail();
    _revBytes(transition['signature_b64'], 64);
    if (registryRoot(registry) != transition['resulting_registry_root']) {
      _revFail();
    }
    final recipients =
        registry
            .where((e) => e['state'] == 'ACTIVE')
            .map((e) => e['device_id'] as String)
            .toList()
          ..sort();
    final expected = <String, Object?>{
      'format': 'atlasvault-rotation-plan',
      'version': 1,
      'account_id': transition['account_id'],
      'vault_id': transition['vault_id'],
      'previous_epoch': _revNumber(transition['key_epoch']),
      'new_epoch': (transition['key_epoch']! as int) + 1,
      'prior_registry_root': transition['prior_registry_root'],
      'resulting_registry_root': transition['resulting_registry_root'],
      'state_root': stateRoot,
      'initiator_device_id': transition['initiator_device_id'],
      'revocation_root': transition['root'],
      'recipients': recipients,
    };
    _exact(plan, expected.keys.toSet());
    for (final key in expected.keys) {
      if (jsonEncode(plan[key]) != jsonEncode(expected[key])) {
        _revFail('ATLAS_ROTATION_PLAN_REJECTED');
      }
    }
  });
}

/// One owner per file. Genesis is pinned by the caller's trusted P6 checkpoint.
final class AtlasVaultRevocationRegistry {
  AtlasVaultRevocationRegistry({
    required File file,
    required Uint8List encryptionKey,
    required String accountId,
    required String vaultId,
    required int keyEpoch,
    required List<Map<String, Object?>> registry,
    required String stateRoot,
  }) : _file = _EncryptedQueueFile(
         file,
         encryptionKey,
         kind: 'device-revocation-v1',
       ),
       _initial = _revEntries(jsonDecode(jsonEncode(registry))),
       _context = {
         'account_id': _revID(accountId),
         'vault_id': _revID(vaultId),
         'key_epoch': _revNumber(keyEpoch),
         'state_root': _commitmentHex(stateRoot),
         'registry_root': AtlasVaultRevocation.registryRoot(registry),
       };
  final _EncryptedQueueFile _file;
  final List<Map<String, Object?>> _initial;
  final Map<String, Object?> _context;
  bool _busy = false;
  Future<T> _exclusive<T>(Future<T> Function() body) async {
    if (_busy) _revFail();
    _busy = true;
    try {
      return await body();
    } on AtlasVaultRevocationException {
      rethrow;
    } catch (_) {
      _revFail();
    } finally {
      _busy = false;
    }
  }

  void _checkContext(Map<String, Object?> transition) {
    for (final f in ['account_id', 'vault_id', 'key_epoch']) {
      if (transition[f] != _context[f]) _revFail();
    }
    if (transition['sequence'] is! int || transition['sequence'] != 1) {
      _revFail();
    }
  }

  Future<Map<String, Object?>> _read() async {
    if (!await _file.file.exists() || await _file.file.length() > 1024 * 1024) {
      _revFail();
    }
    final v = await _file.read({});
    _exact(v, {'context', 'transition', 'recovery_pending'});
    final context = Map<String, Object?>.from(v['context'] as Map);
    _exact(context, _context.keys.toSet());
    if (_context.keys.any((f) => context[f] != _context[f]) ||
        v['recovery_pending'] is! bool) {
      _revFail();
    }
    if (v['transition'] != null) {
      final t = Map<String, Object?>.from(v['transition'] as Map);
      _checkContext(t);
      await AtlasVaultRevocation.verify(t, _initial);
    }
    return v;
  }

  Future<Map<String, Object?>> _snapshot() async {
    final v = await _read(),
        t = v['transition'] == null
            ? null
            : Map<String, Object?>.from(v['transition'] as Map);
    final entries = t == null
        ? _initial
        : await AtlasVaultRevocation.verify(t, _initial);
    return _revCopy({
      'registry': entries,
      'root': AtlasVaultRevocation.registryRoot(entries),
      'sequence': t == null ? 0 : 1,
      'status': v['recovery_pending'] == true
          ? 'RECOVERY_PENDING'
          : t == null
          ? 'ACTIVE'
          : 'REVOCATION_PENDING',
      'transition': t,
    });
  }

  Future<void> initialize() => _exclusive(() async {
    if (await _file.file.exists()) _revFail();
    await _file.write({
      'context': _context,
      'transition': null,
      'recovery_pending': false,
    });
  });
  Future<Map<String, Object?>> snapshot() => _exclusive(_snapshot);
  Future<Map<String, Object?>> prepare(String target, String initiator) =>
      _exclusive(() async {
        final state = await _snapshot();
        if (state['status'] != 'ACTIVE') _revFail('ATLAS_REMOVAL_PENDING');
        final after = AtlasVaultRevocation._removed(
          _revEntries(state['registry']),
          target,
          initiator,
        );
        return {
          'format': _revFormat,
          'version': 1,
          'account_id': _context['account_id'],
          'vault_id': _context['vault_id'],
          'target_device_id': target,
          'initiator_device_id': initiator,
          'prior_registry_root': state['root'],
          'resulting_registry_root': AtlasVaultRevocation.registryRoot(after),
          'key_epoch': _context['key_epoch'],
          'sequence': 1,
          'authorization_category': 'DEVICE_PRESENCE',
        };
      });
  Future<bool> commit(
    Map<String, Object?> input, {
    bool Function()? authorizationStillValid,
  }) => _exclusive(() async {
    final transition = _revCopy(input), value = await _read();
    if (value['recovery_pending'] == true) _revFail('ATLAS_REMOVAL_PENDING');
    _checkContext(transition);
    await AtlasVaultRevocation.verify(transition, _initial);
    if (value['transition'] != null) {
      final old = Map<String, Object?>.from(value['transition'] as Map);
      if (old['root'] == transition['root']) return false;
      _revFail('ATLAS_REVOCATION_CONFLICT');
    }
    if (authorizationStillValid != null && !authorizationStillValid()) {
      _revFail('ATLAS_REMOVAL_AUTHORIZATION');
    }
    value['transition'] = transition;
    await _file.write(value);
    return true;
  });
  Future<void> fence() => _exclusive(() async {
    final value = await _read();
    value['recovery_pending'] = true;
    await _file.write(value);
  });
}

final class AtlasVaultRemovalController {
  AtlasVaultRemovalController({
    required this.registry,
    required this.initiator,
    required this._authorize,
    required this._sign,
    Duration Function()? clock,
    this._history,
  }) : _clock = clock ?? (Stopwatch()..start()).elapsedProvider;
  final AtlasVaultRevocationRegistry registry;
  final String initiator;
  final Future<Object?> Function() _authorize;
  final Future<Uint8List> Function(Uint8List) _sign;
  final Duration Function() _clock;
  final AtlasVaultGuardedSyncState? _history;
  Future<T> _historyGuard<T>(Future<T> Function() operation) async {
    if (_history == null) return operation();
    try {
      final views = await _history.exportEvidence();
      if (views.isEmpty ||
          [
            'account_id',
            'vault_id',
            'key_epoch',
          ].any((k) => views.last[k] != registry._context[k]) ||
          views.last['root'] != registry._context['state_root']) {
        _revFail('ATLAS_REMOVAL_PENDING');
      }
      return await _history.automaticSync(operation);
    } catch (_) {
      await registry.fence();
      _revFail('ATLAS_REMOVAL_PENDING');
    }
  }

  String? _target;
  int _generation = 0;
  bool _busy = false;
  void select(String target) {
    _target = _revID(target);
    _generation++;
  }

  void cancel() {
    _generation++;
  }

  Future<Map<String, Object?>> remove(String confirmedTarget) async {
    if (_busy || confirmedTarget != _target) {
      _revFail('ATLAS_REMOVAL_AUTHORIZATION');
    }
    _busy = true;
    final generation = _generation, started = _clock();
    bool fresh() {
      final elapsed = _clock() - started;
      return generation == _generation &&
          !elapsed.isNegative &&
          elapsed < const Duration(seconds: 60);
    }

    try {
      final unsigned = await _historyGuard(
        () => registry.prepare(confirmedTarget, initiator),
      );
      Future<void> check() async {
        final current = await _historyGuard(
          () => registry.prepare(confirmedTarget, initiator),
        );
        if (!fresh() || unsigned.keys.any((k) => current[k] != unsigned[k])) {
          _revFail('ATLAS_REMOVAL_AUTHORIZATION');
        }
      }

      if (await _authorize().timeout(const Duration(seconds: 60)) != true) {
        _revFail('ATLAS_REMOVAL_AUTHORIZATION');
      }
      await check();
      final root = AtlasVaultRevocation._root(unsigned),
          signature = await _sign(
            AtlasVaultRevocation._message(AtlasVaultRevocation._root(unsigned)),
          ).timeout(const Duration(seconds: 60));
      await check();
      final signed = {
        ...unsigned,
        'root': root,
        'signature_b64': base64Encode(signature),
      };
      await _historyGuard(
        () => registry.commit(signed, authorizationStillValid: fresh),
      );
      cancel();
      return signed;
    } catch (_) {
      _revFail('ATLAS_REMOVAL_AUTHORIZATION');
    } finally {
      _busy = false;
    }
  }
}

extension on Stopwatch {
  Duration elapsedProvider() => elapsed;
}
