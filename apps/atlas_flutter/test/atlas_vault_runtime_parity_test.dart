import 'dart:convert';

import 'package:atlas/atlas_vault.dart' as vault;
import 'package:atlas/atlas_vault_apple.dart';
import 'package:atlas/atlas_vault_android.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';

const _vaultId = 'vault-c29';
const _timestamp = '2026-09-04T00:00:00Z';

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  test('Apple adapters fail closed through the production channel', () async {
    const channel = MethodChannel(atlasVaultAppleMethodChannelName);
    final calls = <MethodCall>[];
    TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger
        .setMockMethodCallHandler(channel, (call) async {
          calls.add(call);
          throw PlatformException(code: 'UNAVAILABLE');
        });
    addTearDown(() {
      TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger
          .setMockMethodCallHandler(channel, null);
    });

    await expectLater(
      AtlasAppleVaultSecureKeyStore().loadVaultKey(_vaultId),
      throwsA(isA<AtlasVaultAppleStorageException>()),
    );
    await expectLater(
      AtlasAppleVaultLocalStoreIO().read(_vaultId),
      throwsA(isA<AtlasVaultAppleStorageException>()),
    );
    await expectLater(
      AtlasAppleSelectedVaultStore().read(),
      throwsA(isA<AtlasVaultAppleStorageException>()),
    );
    expect(calls.map((call) => call.method), <String>[
      'loadVaultKey',
      'readLocalStore',
      'readSelectedVault',
    ]);
  });

  test(
    'all intended families support encrypted create update delete',
    () async {
      final store = _MemoryStore(_emptyStore());
      final ids = <String>[
        for (var index = 1; index <= 20; index += 1)
          '10000000-0000-4000-8000-${index.toString().padLeft(12, '0')}',
      ];
      final keyStore = _MemoryKeyStore(_key());
      final runtime = AtlasVaultPrivateStateRuntime(
        secureKeyStore: keyStore,
        localStoreIO: store,
        uuidProvider: () => ids.removeAt(0),
        nonceProvider: () =>
            Uint8List.fromList(List<int>.filled(12, ids.length)),
        now: () => DateTime.parse(_timestamp),
      );
      expect(
        await runtime.activateExisting(_vaultId),
        AtlasVaultActivationResult.activated,
      );

      final original = _envelopes();
      for (final envelope in original) {
        await runtime.createRecord(envelope);
      }
      final created = await runtime.read();
      expect(
        created.records.map((record) => record.envelope.type).toSet(),
        vault.AtlasVaultPayloadType.values.toSet(),
      );

      for (final record in created.records) {
        await runtime.updateRecord(
          recordId: record.recordId,
          currentRevision: record.revision,
          envelope: record.envelope,
        );
      }
      final updated = await runtime.read();
      for (var index = 0; index < updated.records.length; index += 1) {
        expect(
          updated.records[index].parentRevision,
          created.records[index].revision,
        );
      }

      for (final record in updated.records) {
        await runtime.deleteRecord(
          recordId: record.recordId,
          currentRevision: record.revision,
        );
      }
      final deleted = await runtime.read();
      expect(deleted.records, isEmpty);
      expect(
        deleted.tombstones,
        hasLength(vault.AtlasVaultPayloadType.values.length),
      );
      final serialized = utf8.decode(store.value.canonicalBytes());
      for (final sentinel in <String>[
        'PRIVATE_SEARCH',
        'PRIVATE_JOB',
        'PRIVATE_NOTE',
        'PRIVATE_SNIPPET',
        'PRIVATE_DRAFT',
      ]) {
        expect(serialized, isNot(contains(sentinel)));
      }

      await runtime.deactivate();
      final restarted = AtlasVaultPrivateStateRuntime(
        secureKeyStore: keyStore,
        localStoreIO: store,
      );
      expect(
        await restarted.activateExisting(_vaultId),
        AtlasVaultActivationResult.activated,
      );
      final restored = await restarted.read();
      expect(restored.records, isEmpty);
      expect(
        restored.tombstones,
        hasLength(vault.AtlasVaultPayloadType.values.length),
      );
      await restarted.deactivate();
    },
  );
}

final class _MemoryKeyStore implements AtlasVaultSecureKeyStore {
  _MemoryKeyStore(this.value);
  Uint8List? value;

  @override
  Future<void> createVaultKey(String vaultId, Uint8List vaultKey) async {
    if (value != null) throw StateError('duplicate');
    value = Uint8List.fromList(vaultKey);
  }

  @override
  Future<Uint8List?> loadVaultKey(String vaultId) async =>
      value == null ? null : Uint8List.fromList(value!);

  @override
  Future<bool> containsVaultKey(String vaultId) async => value != null;

  @override
  Future<void> deleteVaultKey(String vaultId) async => value = null;
}

final class _MemoryStore implements AtlasVaultLocalStoreIO {
  _MemoryStore(this.value);
  vault.AtlasVaultLocalStore value;

  @override
  Future<vault.AtlasVaultLocalStore?> read(String vaultId) async => value;

  @override
  Future<void> create(String vaultId, vault.AtlasVaultLocalStore store) async {
    value = store;
  }

  @override
  Future<void> replace(
    String vaultId,
    vault.AtlasVaultLocalStore store, {
    required String expectedSha256,
  }) async {
    final actual = await vault.atlasVaultSha256Hex(value.canonicalBytes());
    if (actual != expectedSha256) throw StateError('stale');
    value = store;
  }

  @override
  Future<void> delete(String vaultId) async {}
}

Uint8List _key() => Uint8List.fromList(List<int>.generate(32, (i) => i + 1));

vault.AtlasVaultLocalStore _emptyStore() =>
    vault.AtlasVaultLocalStore.fromJson(<String, Object?>{
      'format': 'atlasvault-local-store',
      'version': 1,
      'store_id': '20000000-0000-4000-8000-000000000001',
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
      'records': <Object?>[],
    });

List<vault.AtlasVaultPayloadEnvelope> _envelopes() =>
    <Map<String, Object?>>[
          {
            'type': 'saved_search',
            'payload': {
              'name': 'PRIVATE_SEARCH',
              'summary': 'private',
              'request': {
                'status': <String>[],
                'organizations': <String>[],
                'source_ids': <String>[],
                'cities': <String>[],
                'countries_iso3': <String>[],
                'national_international': <String>[],
                'grade_codes': <String>[],
                'ccog_families': <String>[],
                'capability_tags': <String>[],
                'contract_groups': <String>[],
                'seniority_groups': <String>[],
                'work_modalities': <String>[],
                'volunteer_kinds': <String>[],
                'unv_categories': <String>[],
                'unv_volunteer_types': <String>[],
                'include_low_confidence': false,
                'include_facets': false,
                'limit': 1,
                'offset': 0,
                'sort': 'closing_date_asc',
              },
            },
          },
          {
            'type': 'saved_job',
            'payload': {'job_key': 'PRIVATE_JOB', 'status': 'saved'},
          },
          {
            'type': 'application_note',
            'payload': {
              'body': 'PRIVATE_NOTE',
              'note_kind': 'general',
              'created_at': _timestamp,
              'updated_at': _timestamp,
            },
          },
          {
            'type': 'profile_snippet',
            'payload': {
              'title': 'Snippet',
              'body': 'PRIVATE_SNIPPET',
              'tags': <String>[],
              'created_at': _timestamp,
              'updated_at': _timestamp,
            },
          },
          {
            'type': 'draft_metadata',
            'payload': {
              'target_system': 'INSPIRA',
              'document_type': 'cover_letter',
              'generated_document_reference': 'PRIVATE_DRAFT',
              'draft_status': 'draft',
              'generated_at': _timestamp,
            },
          },
        ]
        .map(
          (value) => vault.AtlasVaultPayloadEnvelope.fromJson({
            ...value,
            'payload_schema': 1,
            'client_created_at': _timestamp,
            'client_updated_at': _timestamp,
          }),
        )
        .toList();
