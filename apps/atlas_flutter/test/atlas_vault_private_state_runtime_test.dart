import 'dart:async';
import 'dart:convert';
import 'dart:typed_data';

import 'package:atlas/atlas.dart';
import 'package:atlas/atlas_vault.dart' as vault;
import 'package:atlas/atlas_vault_android.dart';
import 'package:flutter_test/flutter_test.dart';

const _vaultId = 'vault-alpha';
const _timestamp = '2026-07-28T12:00:00Z';
const _nextTimestamp = '2026-07-28T12:00:01Z';
const _keyId = 'primary-android-local-key-v1';

void main() {
  test('construction performs no secure-key or local-store operation', () {
    final keyStore = _FakeSecureKeyStore(key: _vaultKey());
    final storeIO = _FakeLocalStoreIO();

    AtlasVaultPrivateStateRuntime(
      secureKeyStore: keyStore,
      localStoreIO: storeIO,
    );

    expect(keyStore.calls, isEmpty);
    expect(storeIO.calls, isEmpty);
  });

  test('activation fails closed when key or store is absent', () async {
    final missingKeyStore = _FakeSecureKeyStore();
    final localStore = _FakeLocalStoreIO(store: _emptyStore());
    final missingKeyRuntime = AtlasVaultPrivateStateRuntime(
      secureKeyStore: missingKeyStore,
      localStoreIO: localStore,
    );

    expect(
      await missingKeyRuntime.activateExisting(_vaultId),
      AtlasVaultActivationResult.failed,
    );
    expect(missingKeyRuntime.isActive, isFalse);
    expect(localStore.calls, isEmpty);

    final presentKeyStore = _FakeSecureKeyStore(key: _vaultKey());
    final missingStoreRuntime = AtlasVaultPrivateStateRuntime(
      secureKeyStore: presentKeyStore,
      localStoreIO: _FakeLocalStoreIO(),
    );
    expect(
      await missingStoreRuntime.activateExisting(_vaultId),
      AtlasVaultActivationResult.failed,
    );
    expect(missingStoreRuntime.isActive, isFalse);
  });

  test(
    'activation fails closed when store metadata names another vault',
    () async {
      final storeJson = _emptyStore().toJson();
      final metadata = Map<String, Object?>.from(
        storeJson['vault_metadata']! as Map<String, Object?>,
      );
      metadata['vault_id'] = 'vault-other';
      storeJson['vault_metadata'] = metadata;
      final runtime = AtlasVaultPrivateStateRuntime(
        secureKeyStore: _FakeSecureKeyStore(key: _vaultKey()),
        localStoreIO: _FakeLocalStoreIO(
          store: vault.AtlasVaultLocalStore.fromJson(storeJson),
        ),
      );

      expect(
        await runtime.activateExisting(_vaultId),
        AtlasVaultActivationResult.failed,
      );
      expect(runtime.isActive, isFalse);
    },
  );

  test(
    'activation decrypts supported records and hides other families',
    () async {
      final records = <vault.AtlasVaultEncryptedRecord>[
        await _savedSearchRecord(
          name: 'Programme roles',
          query: 'programme',
          recordId: '10000000-0000-4000-8000-000000000001',
          revision: '20000000-0000-4000-8000-000000000001',
        ),
        await _savedJobRecord(
          jobKey: 'undp:123',
          recordId: '10000000-0000-4000-8000-000000000002',
          revision: '20000000-0000-4000-8000-000000000002',
        ),
        await _applicationNoteRecord(),
      ];
      final runtime = AtlasVaultPrivateStateRuntime(
        secureKeyStore: _FakeSecureKeyStore(key: _vaultKey()),
        localStoreIO: _FakeLocalStoreIO(store: _store(records)),
      );

      final result = await runtime.activateExisting(_vaultId);
      final snapshot = await runtime.read();

      expect(result, AtlasVaultActivationResult.activated);
      expect(runtime.isActive, isTrue);
      expect(snapshot.savedSearches, hasLength(1));
      expect(snapshot.savedSearches.single.name, 'Programme roles');
      expect(snapshot.savedSearches.single.request.text, 'programme');
      expect(snapshot.trackerRecords, hasLength(1));
      expect(snapshot.trackerRecords.single.jobKey, 'undp:123');
      expect(
        () => snapshot.savedSearches.add(
          AtlasSavedSearch(name: 'mutate', request: const AtlasSearchRequest()),
        ),
        throwsUnsupportedError,
      );
      expect(snapshot.toString(), 'AtlasVaultPrivateStateSnapshot(<redacted>)');
      expect(snapshot.toString(), isNot(contains('Programme roles')));
    },
  );

  test('deactivation supersedes a direct runtime activation', () async {
    final enteredLoad = Completer<void>();
    final releaseLoad = Completer<void>();
    final runtime = AtlasVaultPrivateStateRuntime(
      secureKeyStore: _FakeSecureKeyStore(
        key: _vaultKey(),
        enteredLoad: enteredLoad,
        releaseLoad: releaseLoad,
      ),
      localStoreIO: _FakeLocalStoreIO(store: _emptyStore()),
    );

    final activation = runtime.activateExisting(_vaultId);
    await enteredLoad.future;
    await runtime.deactivate();
    expect(runtime.isActive, isFalse);

    expect(
      await runtime.activateExisting(_vaultId),
      AtlasVaultActivationResult.activated,
    );

    releaseLoad.complete();

    expect(await activation, AtlasVaultActivationResult.failed);
    expect(runtime.isActive, isTrue);
    expect((await runtime.read()).savedSearches, isEmpty);
  });

  test(
    'corrupt supported ciphertext fails activation without authority',
    () async {
      final record = await _savedSearchRecord(
        name: 'Private sentinel',
        query: 'secret query',
        recordId: '10000000-0000-4000-8000-000000000003',
        revision: '20000000-0000-4000-8000-000000000003',
      );
      final json = record.toJson();
      final ciphertext = base64Decode(json['ciphertext']! as String);
      ciphertext[0] ^= 0xff;
      json['ciphertext'] = base64Encode(ciphertext);
      final runtime = AtlasVaultPrivateStateRuntime(
        secureKeyStore: _FakeSecureKeyStore(key: _vaultKey()),
        localStoreIO: _FakeLocalStoreIO(
          store: _store(<vault.AtlasVaultEncryptedRecord>[
            vault.AtlasVaultEncryptedRecord.fromJson(json),
          ]),
        ),
      );

      expect(
        await runtime.activateExisting(_vaultId),
        AtlasVaultActivationResult.failed,
      );
      expect(runtime.isActive, isFalse);
      await expectLater(
        runtime.read(),
        throwsA(isA<AtlasVaultPrivateStateException>()),
      );
    },
  );

  test('duplicate active names and tracker keys fail closed', () async {
    final duplicateSearchStore = _store(<vault.AtlasVaultEncryptedRecord>[
      await _savedSearchRecord(
        name: 'Duplicate',
        query: 'first',
        recordId: '10000000-0000-4000-8000-000000000004',
        revision: '20000000-0000-4000-8000-000000000004',
      ),
      await _savedSearchRecord(
        name: 'Duplicate',
        query: 'second',
        recordId: '10000000-0000-4000-8000-000000000005',
        revision: '20000000-0000-4000-8000-000000000005',
      ),
    ]);
    final duplicateSearchRuntime = AtlasVaultPrivateStateRuntime(
      secureKeyStore: _FakeSecureKeyStore(key: _vaultKey()),
      localStoreIO: _FakeLocalStoreIO(store: duplicateSearchStore),
    );
    expect(
      await duplicateSearchRuntime.activateExisting(_vaultId),
      AtlasVaultActivationResult.failed,
    );

    final duplicateTrackerRuntime = AtlasVaultPrivateStateRuntime(
      secureKeyStore: _FakeSecureKeyStore(key: _vaultKey()),
      localStoreIO: _FakeLocalStoreIO(
        store: _store(<vault.AtlasVaultEncryptedRecord>[
          await _savedJobRecord(
            jobKey: 'undp:duplicate',
            recordId: '10000000-0000-4000-8000-000000000006',
            revision: '20000000-0000-4000-8000-000000000006',
          ),
          await _savedJobRecord(
            jobKey: 'undp:duplicate',
            recordId: '10000000-0000-4000-8000-000000000007',
            revision: '20000000-0000-4000-8000-000000000007',
          ),
        ]),
      ),
    );
    expect(
      await duplicateTrackerRuntime.activateExisting(_vaultId),
      AtlasVaultActivationResult.failed,
    );
  });

  test(
    'saved-search create is encrypted and update preserves identity',
    () async {
      final unrelated = await _applicationNoteRecord();
      final storeIO = _FakeLocalStoreIO(
        store: _store(<vault.AtlasVaultEncryptedRecord>[unrelated]),
      );
      final identifiers = <String>[
        '10000000-0000-4000-8000-000000000010',
        '20000000-0000-4000-8000-000000000010',
        '20000000-0000-4000-8000-000000000011',
      ];
      final times = <DateTime>[
        DateTime.parse(_timestamp),
        DateTime.parse(_nextTimestamp),
      ];
      var nonceSeed = 10;
      final runtime = AtlasVaultPrivateStateRuntime(
        secureKeyStore: _FakeSecureKeyStore(key: _vaultKey()),
        localStoreIO: storeIO,
        uuidProvider: () => identifiers.removeAt(0),
        now: () => times.removeAt(0),
        nonceProvider: () => Uint8List(12)..fillRange(0, 12, nonceSeed++),
      );
      expect(
        await runtime.activateExisting(_vaultId),
        AtlasVaultActivationResult.activated,
      );

      final created = await runtime.saveSearch(
        AtlasSavedSearch(
          name: 'PRIVATE_SEARCH_NAME',
          description: 'PRIVATE_SEARCH_DESCRIPTION',
          request: const AtlasSearchRequest(text: 'PRIVATE_SEARCH_QUERY'),
        ),
      );

      expect(created.savedSearches.single.name, 'PRIVATE_SEARCH_NAME');
      final createdStore = storeIO.store!;
      final createdRecord = createdStore.records.singleWhere(
        (record) => record.id == '10000000-0000-4000-8000-000000000010',
      );
      expect(createdRecord.keyId, _keyId);
      expect(createdRecord.parentRevision, isNull);
      expect(createdStore.records.first, unrelated);
      final createdBytes = utf8.decode(createdStore.canonicalBytes());
      expect(createdBytes, isNot(contains('PRIVATE_SEARCH_NAME')));
      expect(createdBytes, isNot(contains('PRIVATE_SEARCH_QUERY')));
      expect(createdBytes, isNot(contains('saved_search')));
      final createdEnvelope = await _openEnvelope(createdRecord);
      expect(createdEnvelope.type, vault.AtlasVaultPayloadType.savedSearch);

      final oldRevision = createdRecord.revision;
      final updated = await runtime.saveSearch(
        AtlasSavedSearch(
          name: 'PRIVATE_SEARCH_NAME',
          description: 'PRIVATE_SEARCH_DESCRIPTION_UPDATED',
          request: const AtlasSearchRequest(text: 'PRIVATE_QUERY_UPDATED'),
          createdAt: _timestamp,
          updatedAt: _nextTimestamp,
        ),
      );

      expect(
        updated.savedSearches.single.request.text,
        'PRIVATE_QUERY_UPDATED',
      );
      final updatedRecord = storeIO.store!.records.singleWhere(
        (record) => record.id == createdRecord.id,
      );
      expect(updatedRecord.id, createdRecord.id);
      expect(updatedRecord.keyId, createdRecord.keyId);
      expect(updatedRecord.revision, isNot(oldRevision));
      expect(updatedRecord.parentRevision, oldRevision);
      expect(storeIO.store!.records.first, unrelated);
      expect(storeIO.replaceExpectedDigests, hasLength(2));
    },
  );

  test(
    'tracker create is encrypted and does not alter other records',
    () async {
      final savedSearch = await _savedSearchRecord(
        name: 'Existing',
        query: 'existing',
        recordId: '10000000-0000-4000-8000-000000000020',
        revision: '20000000-0000-4000-8000-000000000020',
      );
      final storeIO = _FakeLocalStoreIO(
        store: _store(<vault.AtlasVaultEncryptedRecord>[savedSearch]),
      );
      final identifiers = <String>[
        '10000000-0000-4000-8000-000000000021',
        '20000000-0000-4000-8000-000000000021',
      ];
      final runtime = AtlasVaultPrivateStateRuntime(
        secureKeyStore: _FakeSecureKeyStore(key: _vaultKey()),
        localStoreIO: storeIO,
        uuidProvider: () => identifiers.removeAt(0),
        now: () => DateTime.parse(_timestamp),
        nonceProvider: () => Uint8List.fromList(List<int>.filled(12, 21)),
      );
      await runtime.activateExisting(_vaultId);

      final snapshot = await runtime.saveTrackerRecord(
        AtlasApplicationRecord(
          id: 'local-app-record',
          jobKey: 'undp:PRIVATE_JOB_KEY',
          status: 'saved',
          notes: 'PRIVATE_TRACKER_NOTE',
          updatedAt: _timestamp,
        ),
      );

      expect(snapshot.trackerRecords.single.jobKey, 'undp:PRIVATE_JOB_KEY');
      expect(storeIO.store!.records.first, savedSearch);
      final bytes = utf8.decode(storeIO.store!.canonicalBytes());
      expect(bytes, isNot(contains('PRIVATE_JOB_KEY')));
      expect(bytes, isNot(contains('PRIVATE_TRACKER_NOTE')));
      expect(bytes, isNot(contains('saved_job')));
    },
  );

  test('stale CAS retains the previously committed snapshot', () async {
    final storeIO = _FakeLocalStoreIO(
      store: _emptyStore(),
      rejectReplace: true,
    );
    final identifiers = <String>[
      '10000000-0000-4000-8000-000000000030',
      '20000000-0000-4000-8000-000000000030',
    ];
    final runtime = AtlasVaultPrivateStateRuntime(
      secureKeyStore: _FakeSecureKeyStore(key: _vaultKey()),
      localStoreIO: storeIO,
      uuidProvider: () => identifiers.removeAt(0),
      now: () => DateTime.parse(_timestamp),
      nonceProvider: () => Uint8List.fromList(List<int>.filled(12, 30)),
    );
    await runtime.activateExisting(_vaultId);

    await expectLater(
      runtime.saveSearch(
        AtlasSavedSearch(
          name: 'CAS_PRIVATE_SENTINEL',
          request: const AtlasSearchRequest(text: 'stale'),
        ),
      ),
      throwsA(isA<AtlasVaultPrivateStateException>()),
    );

    expect((await runtime.read()).savedSearches, isEmpty);
    expect(storeIO.store!.records, isEmpty);
  });

  test(
    'concurrent saved-search create is rejected before corrupting the store',
    () async {
      final storeIO = _FakeLocalStoreIO(store: _emptyStore());
      final firstIdentifiers = <String>[
        '10000000-0000-4000-8000-000000000031',
        '20000000-0000-4000-8000-000000000031',
      ];
      final secondIdentifiers = <String>[
        '10000000-0000-4000-8000-000000000032',
        '20000000-0000-4000-8000-000000000032',
      ];
      final first = AtlasVaultPrivateStateRuntime(
        secureKeyStore: _FakeSecureKeyStore(key: _vaultKey()),
        localStoreIO: storeIO,
        uuidProvider: () => firstIdentifiers.removeAt(0),
        now: () => DateTime.parse(_timestamp),
        nonceProvider: () => Uint8List.fromList(List<int>.filled(12, 31)),
      );
      final stale = AtlasVaultPrivateStateRuntime(
        secureKeyStore: _FakeSecureKeyStore(key: _vaultKey()),
        localStoreIO: storeIO,
        uuidProvider: () => secondIdentifiers.removeAt(0),
        now: () => DateTime.parse(_timestamp),
        nonceProvider: () => Uint8List.fromList(List<int>.filled(12, 32)),
      );
      await first.activateExisting(_vaultId);
      await stale.activateExisting(_vaultId);
      await first.saveSearch(
        AtlasSavedSearch(
          name: 'Concurrent search',
          request: const AtlasSearchRequest(text: 'first'),
        ),
      );
      final committedBytes = storeIO.store!.canonicalBytes();
      final replaceCount = storeIO.calls
          .where((call) => call == 'replace')
          .length;

      await expectLater(
        stale.saveSearch(
          AtlasSavedSearch(
            name: 'Concurrent search',
            request: const AtlasSearchRequest(text: 'stale'),
          ),
        ),
        throwsA(isA<AtlasVaultPrivateStateException>()),
      );

      expect(
        storeIO.calls.where((call) => call == 'replace').length,
        replaceCount,
      );
      expect(storeIO.store!.canonicalBytes(), orderedEquals(committedBytes));
      expect(storeIO.store!.records, hasLength(1));
    },
  );

  test(
    'concurrent tracker create is rejected before corrupting the store',
    () async {
      final storeIO = _FakeLocalStoreIO(store: _emptyStore());
      final firstIdentifiers = <String>[
        '10000000-0000-4000-8000-000000000033',
        '20000000-0000-4000-8000-000000000033',
      ];
      final secondIdentifiers = <String>[
        '10000000-0000-4000-8000-000000000034',
        '20000000-0000-4000-8000-000000000034',
      ];
      final first = AtlasVaultPrivateStateRuntime(
        secureKeyStore: _FakeSecureKeyStore(key: _vaultKey()),
        localStoreIO: storeIO,
        uuidProvider: () => firstIdentifiers.removeAt(0),
        now: () => DateTime.parse(_timestamp),
        nonceProvider: () => Uint8List.fromList(List<int>.filled(12, 33)),
      );
      final stale = AtlasVaultPrivateStateRuntime(
        secureKeyStore: _FakeSecureKeyStore(key: _vaultKey()),
        localStoreIO: storeIO,
        uuidProvider: () => secondIdentifiers.removeAt(0),
        now: () => DateTime.parse(_timestamp),
        nonceProvider: () => Uint8List.fromList(List<int>.filled(12, 34)),
      );
      await first.activateExisting(_vaultId);
      await stale.activateExisting(_vaultId);
      await first.saveTrackerRecord(
        AtlasApplicationRecord(
          id: '',
          jobKey: 'undp:concurrent',
          status: 'saved',
        ),
      );
      final committedBytes = storeIO.store!.canonicalBytes();
      final replaceCount = storeIO.calls
          .where((call) => call == 'replace')
          .length;

      await expectLater(
        stale.saveTrackerRecord(
          AtlasApplicationRecord(
            id: '',
            jobKey: 'undp:concurrent',
            status: 'applied',
          ),
        ),
        throwsA(isA<AtlasVaultPrivateStateException>()),
      );

      expect(
        storeIO.calls.where((call) => call == 'replace').length,
        replaceCount,
      );
      expect(storeIO.store!.canonicalBytes(), orderedEquals(committedBytes));
      expect(storeIO.store!.records, hasLength(1));
    },
  );

  test('deactivation drains mutation and clears session authority', () async {
    final enteredReplace = Completer<void>();
    final releaseReplace = Completer<void>();
    final storeIO = _FakeLocalStoreIO(
      store: _emptyStore(),
      enteredReplace: enteredReplace,
      releaseReplace: releaseReplace,
    );
    final identifiers = <String>[
      '10000000-0000-4000-8000-000000000040',
      '20000000-0000-4000-8000-000000000040',
    ];
    final runtime = AtlasVaultPrivateStateRuntime(
      secureKeyStore: _FakeSecureKeyStore(key: _vaultKey()),
      localStoreIO: storeIO,
      uuidProvider: () => identifiers.removeAt(0),
      now: () => DateTime.parse(_timestamp),
      nonceProvider: () => Uint8List.fromList(List<int>.filled(12, 40)),
    );
    await runtime.activateExisting(_vaultId);
    final mutation = runtime.saveSearch(
      AtlasSavedSearch(
        name: 'Drain me',
        request: const AtlasSearchRequest(text: 'drain'),
      ),
    );
    await enteredReplace.future;

    var deactivated = false;
    final deactivation = runtime.deactivate().then((_) {
      deactivated = true;
    });
    await Future<void>.value();
    expect(deactivated, isFalse);

    releaseReplace.complete();
    await mutation;
    await deactivation;

    expect(runtime.isActive, isFalse);
    await expectLater(
      runtime.read(),
      throwsA(isA<AtlasVaultPrivateStateException>()),
    );
  });

  test('errors and descriptions never echo private input', () async {
    const failure = AtlasVaultPrivateStateException();
    expect(failure.toString(), 'AtlasVault private-state operation failed.');
    expect(failure.toString(), isNot(contains('PRIVATE')));
    expect(
      AtlasVaultActivationResult.values.map((value) => value.toString()),
      everyElement(isNot(contains(_vaultId))),
    );
  });
}

final class _FakeSecureKeyStore implements AtlasVaultSecureKeyStore {
  _FakeSecureKeyStore({Uint8List? key, this.enteredLoad, this.releaseLoad})
    : _key = key == null ? null : Uint8List.fromList(key);

  Uint8List? _key;
  final Completer<void>? enteredLoad;
  final Completer<void>? releaseLoad;
  final List<String> calls = <String>[];

  @override
  Future<bool> containsVaultKey(String vaultId) async {
    calls.add('contains');
    return _key != null;
  }

  @override
  Future<void> createVaultKey(String vaultId, Uint8List vaultKey) async {
    calls.add('create');
    if (_key != null) {
      throw const AtlasVaultAndroidStorageException();
    }
    _key = Uint8List.fromList(vaultKey);
  }

  @override
  Future<void> deleteVaultKey(String vaultId) async {
    calls.add('delete');
    _key = null;
  }

  @override
  Future<Uint8List?> loadVaultKey(String vaultId) async {
    calls.add('load');
    if (enteredLoad != null && !enteredLoad!.isCompleted) {
      enteredLoad!.complete();
      if (releaseLoad != null) {
        await releaseLoad!.future;
      }
    }
    return _key == null ? null : Uint8List.fromList(_key!);
  }
}

final class _FakeLocalStoreIO implements AtlasVaultLocalStoreIO {
  _FakeLocalStoreIO({
    this.store,
    this.rejectReplace = false,
    this.enteredReplace,
    this.releaseReplace,
  });

  vault.AtlasVaultLocalStore? store;
  final bool rejectReplace;
  final Completer<void>? enteredReplace;
  final Completer<void>? releaseReplace;
  final List<String> calls = <String>[];
  final List<String> replaceExpectedDigests = <String>[];

  @override
  Future<void> create(String vaultId, vault.AtlasVaultLocalStore value) async {
    calls.add('create');
    if (store != null) {
      throw const AtlasVaultAndroidStorageException();
    }
    store = value;
  }

  @override
  Future<void> delete(String vaultId) async {
    calls.add('delete');
    store = null;
  }

  @override
  Future<vault.AtlasVaultLocalStore?> read(String vaultId) async {
    calls.add('read');
    return store;
  }

  @override
  Future<void> replace(
    String vaultId,
    vault.AtlasVaultLocalStore value, {
    required String expectedSha256,
  }) async {
    calls.add('replace');
    replaceExpectedDigests.add(expectedSha256);
    final current = store;
    if (current == null ||
        rejectReplace ||
        await vault.atlasVaultSha256Hex(current.canonicalBytes()) !=
            expectedSha256) {
      throw const AtlasVaultAndroidStorageException();
    }
    enteredReplace?.complete();
    if (releaseReplace != null) {
      await releaseReplace!.future;
    }
    store = value;
  }
}

Uint8List _vaultKey() {
  return Uint8List.fromList(
    List<int>.generate(32, (index) => (index + 1) & 0xff),
  );
}

vault.AtlasVaultLocalStore _emptyStore() {
  return _store(const <vault.AtlasVaultEncryptedRecord>[]);
}

vault.AtlasVaultLocalStore _store(
  List<vault.AtlasVaultEncryptedRecord> records,
) {
  return vault.AtlasVaultLocalStore.fromJson(<String, Object?>{
    'format': 'atlasvault-local-store',
    'version': 1,
    'store_id': '99999999-8888-4777-8666-555555555555',
    'created_at': _timestamp,
    'updated_at': _timestamp,
    'vault_metadata': <String, Object?>{
      'format': 'atlas-vault',
      'version': 1,
      'vault_id': _vaultId,
      'crypto': <String, Object?>{
        'record_aead': 'AES-256-GCM',
        'kdf': 'Argon2id',
        'subkey_kdf': 'HKDF-SHA256',
        'key_wrap_aead': 'AES-256-GCM',
      },
      'key_wraps': <Object?>[],
    },
    'records': <Object?>[for (final record in records) record.toJson()],
  });
}

Future<vault.AtlasVaultEncryptedRecord> _savedSearchRecord({
  required String name,
  required String query,
  required String recordId,
  required String revision,
}) {
  return _sealedRecord(
    recordId: recordId,
    revision: revision,
    nonceByte: int.parse(recordId.substring(recordId.length - 2), radix: 16),
    envelope: vault.AtlasVaultPayloadEnvelope.fromJson(<String, Object?>{
      'type': 'saved_search',
      'payload_schema': 1,
      'payload': <String, Object?>{
        'name': name,
        'summary': query,
        'description': 'Fixture saved search',
        'request': const AtlasSearchRequest(text: 'placeholder').toJson().map(
          (key, value) =>
              MapEntry<String, Object?>(key, key == 'text' ? query : value),
        ),
        'created_at': _timestamp,
        'updated_at': _timestamp,
      },
      'client_created_at': _timestamp,
      'client_updated_at': _timestamp,
    }),
  );
}

Future<vault.AtlasVaultEncryptedRecord> _savedJobRecord({
  required String jobKey,
  required String recordId,
  required String revision,
}) {
  return _sealedRecord(
    recordId: recordId,
    revision: revision,
    nonceByte: int.parse(recordId.substring(recordId.length - 2), radix: 16),
    envelope: vault.AtlasVaultPayloadEnvelope.fromJson(<String, Object?>{
      'type': 'saved_job',
      'payload_schema': 1,
      'payload': <String, Object?>{
        'id': 'fixture-application',
        'job_key': jobKey,
        'status': 'saved',
        'notes': 'Fixture note',
        'updated_at': _timestamp,
      },
      'client_created_at': _timestamp,
      'client_updated_at': _timestamp,
    }),
  );
}

Future<vault.AtlasVaultEncryptedRecord> _applicationNoteRecord() {
  return _sealedRecord(
    recordId: '10000000-0000-4000-8000-000000000099',
    revision: '20000000-0000-4000-8000-000000000099',
    nonceByte: 99,
    envelope: vault.AtlasVaultPayloadEnvelope.fromJson(<String, Object?>{
      'type': 'application_note',
      'payload_schema': 1,
      'payload': <String, Object?>{
        'body': 'UNEXPOSED_PRIVATE_NOTE',
        'note_kind': 'general',
        'created_at': _timestamp,
        'updated_at': _timestamp,
      },
      'client_created_at': _timestamp,
      'client_updated_at': _timestamp,
    }),
  );
}

Future<vault.AtlasVaultEncryptedRecord> _sealedRecord({
  required String recordId,
  required String revision,
  required int nonceByte,
  required vault.AtlasVaultPayloadEnvelope envelope,
}) async {
  final template = vault.AtlasVaultEncryptedRecord.fromJson(<String, Object?>{
    'id': recordId,
    'schema_version': 1,
    'revision': revision,
    'parent_revision': null,
    'deleted': false,
    'key_id': _keyId,
    'nonce': base64Encode(Uint8List(12)..fillRange(0, 12, nonceByte & 0xff)),
    'ciphertext': base64Encode(Uint8List(16)),
  });
  return vault.sealAtlasVaultRecord(
    plaintext: envelope.canonicalBytes(),
    vaultKey: _vaultKey(),
    vaultId: _vaultId,
    record: template,
  );
}

Future<vault.AtlasVaultPayloadEnvelope> _openEnvelope(
  vault.AtlasVaultEncryptedRecord record,
) async {
  final plaintext = await vault.openAtlasVaultRecord(
    vaultKey: _vaultKey(),
    vaultId: _vaultId,
    record: record,
  );
  try {
    return vault.AtlasVaultPayloadEnvelope.decodeJson(utf8.decode(plaintext));
  } finally {
    plaintext.fillRange(0, plaintext.length, 0);
  }
}
