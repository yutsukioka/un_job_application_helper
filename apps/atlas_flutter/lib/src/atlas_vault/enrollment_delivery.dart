part of 'sync_queue.dart';

final class AtlasVaultEnrollmentDeliveryException implements Exception {
  const AtlasVaultEnrollmentDeliveryException();
  @override
  String toString() => 'ATLAS_ENROLLMENT_DELIVERY_REJECTED';
}

/// D100 recipient-only delivery; D087/D089 and the HPKE implementation are unchanged.
abstract final class AtlasVaultEnrollmentDelivery {
  static final _installing = <String>{};
  static Never _reject() => throw const AtlasVaultEnrollmentDeliveryException();
  static Map<String, Object?> _unsigned(Map<String, Object?> p) => {...p}
    ..remove('root')
    ..remove('signature_b64');
  static String _root(Map<String, Object?> p) => _sha256Hex(
    Uint8List.fromList([
      ...ascii.encode('atlasvault-enrollment-delivery-v1\n'),
      ..._canonicalJsonBytes(_unsigned(p)),
    ]),
  );
  static Uint8List _message(String root) => Uint8List.fromList([
    ...ascii.encode('atlasvault-enrollment-delivery-signature-v1\x00'),
    for (var i = 0; i < 64; i += 2)
      int.parse(root.substring(i, i + 2), radix: 16),
  ]);
  static Uint8List _context(Map<String, Object?> p) {
    final body = _unsigned(p)..remove('deliveries');
    return Uint8List.fromList([
      ...ascii.encode('atlasvault-enrollment-delivery-hpke-v1\x00'),
      ...SHA256Digest().process(_canonicalJsonBytes(body)),
    ]);
  }

  static Future<AtlasVaultEpochVault> install(
    Directory directory,
    Map<String, Object?> packet, {
    required Map<String, Object?> pins,
    required Uint8List trustedSigner,
    required identity.AtlasVaultDeviceIdentity recipient,
    required Uint8List agreementPrivateKey,
    required Uint8List storageKey,
  }) async {
    final keys = <int, Uint8List>{},
        private = Uint8List.fromList(agreementPrivateKey);
    RandomAccessFile? lock;
    var claimed = false;
    final path = directory.absolute.path;
    try {
      final encoded = _canonicalJsonBytes(packet);
      if (encoded.length > 2 * 1024 * 1024 || storageKey.length != 32) {
        _reject();
      }
      final p = _object(jsonDecode(utf8.decode(encoded)));
      final expected = _anchorCopy(pins),
          public = Uint8List.fromList(trustedSigner);
      _exact(p, {
        'format',
        'version',
        'hpke_suite',
        'anchor',
        'registry',
        'collection',
        'opaque_b64',
        'historical_authority',
        'deliveries',
        'root',
        'signature_b64',
      });
      if (p['format'] != 'atlasvault-enrollment-delivery' ||
          p['version'] is! int ||
          p['version'] != 1 ||
          p['hpke_suite'] != '0x0020/0x0001/0x0002' ||
          p['root'] != _root(p) ||
          !await Ed25519().verify(
            _message(p['root']! as String),
            signature: Signature(
              _base64(p['signature_b64'], exactLength: 64),
              publicKey: SimplePublicKey(public, type: KeyPairType.ed25519),
            ),
          )) {
        _reject();
      }
      final anchor = _object(p['anchor']),
          checkpoint = _object(anchor['checkpoint']);
      final trust = <String, Object?>{
        for (final k in [
          'account_id',
          'vault_id',
          'collection_id',
          'key_epoch',
        ])
          k: checkpoint[k],
        'trusted_signer_b64': base64Encode(public),
        'registry': p['registry'],
        ...expected,
      };
      final historyKey = Uint8List.fromList(
        (await Hmac.sha256().calculateMac(
          ascii.encode('atlasvault-enrollment-history-v1'),
          secretKey: SecretKey(storageKey),
        )).bytes,
      );
      final history = AtlasVaultAnchoredSyncState(
        file: File('$path/enrollment-history'),
        encryptionKey: historyKey,
        trust: trust,
      );
      historyKey.fillRange(0, historyKey.length, 0);
      await history._verifyAnchor(anchor);
      if (recipient.deviceId != checkpoint['recipient_device_id'] ||
          _sha256Hex(recipient.agreementPublicKey) !=
              checkpoint['recipient_agreement_sha256']) {
        _reject();
      }
      final bodyEncoded = p['opaque_b64'];
      if (bodyEncoded is! String || bodyEncoded.length > 1398104) _reject();
      final bytes = _base64(bodyEncoded),
          collection = AtlasVaultSignedStateCommitment.fromJson(
            _object(p['collection']),
          );
      if (bytes.length > 1024 * 1024 ||
          collection.collectionId != checkpoint['collection_id'] ||
          collection.sequence != checkpoint['sequence'] ||
          collection.root != checkpoint['collection_root'] ||
          collection._value['state_sha256'] !=
              checkpoint['collection_sha256'] ||
          _sha256Hex(bytes) != checkpoint['collection_sha256'] ||
          !await Ed25519().verify(
            _rootSignatureMessage(collection.root),
            signature: Signature(
              _base64(collection._value['signature_b64'], exactLength: 64),
              publicKey: SimplePublicKey(public, type: KeyPairType.ed25519),
            ),
          )) {
        _reject();
      }
      final body = _object(jsonDecode(utf8.decode(bytes)));
      _exact(body, {'format', 'version', 'route', 'records'});
      final rows = _epochRows(body['records']);
      if (body['format'] != 'atlasvault-guarded-collection' ||
          body['version'] is! int ||
          body['version'] != 1 ||
          !['patch', 'snapshot', 'compaction'].contains(body['route']) ||
          rows.length > 256) {
        _reject();
      }
      final epochs = <int>{checkpoint['key_epoch']! as int}, ids = <String>{};
      for (final raw in rows) {
        final record = AtlasVaultOpaqueCiphertextEnvelope.fromJson(raw);
        if (record.version != 1 ||
            record.keyEpoch > (checkpoint['key_epoch']! as int) ||
            !ids.add(record.objectId)) {
          _reject();
        }
        epochs.add(record.keyEpoch);
      }
      if (epochs.length > 32) _reject();
      if (epochs.length > 1) {
        await _verifyHistoricalAuthority(
          _object(p['historical_authority']),
          anchor,
          public,
          _epochRows(p['registry']),
          trust,
          _object(p['collection']),
          bytes,
        );
      } else if (p['historical_authority'] != null) {
        _reject();
      }
      final requiredEpochs = epochs.toList()..sort(),
          deliveries = _epochRows(p['deliveries']);
      if (!_anchorEqual(
        deliveries.map((d) => d['key_epoch']).toList(),
        requiredEpochs,
      )) {
        _reject();
      }
      for (final delivery in deliveries) {
        _exact(delivery, {
          'key_epoch',
          'encapsulated_key_b64',
          'ciphertext_b64',
        });
        final opened = await openAtlasVaultEpochHPKEV2(
          recipientPrivateKey: private,
          sealed: AtlasVaultEpochHPKESealedVaultKeyV2(
            keyEpoch: delivery['key_epoch']! as int,
            encapsulatedKey: _base64(
              delivery['encapsulated_key_b64'],
              exactLength: 32,
            ),
            ciphertext: _base64(delivery['ciphertext_b64'], exactLength: 48),
          ),
          context: _context(p),
          minimumKeyEpoch: delivery['key_epoch']! as int,
        );
        keys[opened.keyEpoch] = opened.vaultKey;
      }
      // In-process exclusion plus the OS file lock serialize new-store publication.
      // The immutable encrypted receipt is persisted before any history component.
      if (!_installing.add(path)) _reject();
      claimed = true;
      await directory.create(recursive: true);
      lock = await File('$path/enrollment.lock').open(mode: FileMode.append);
      await lock.lock(FileLock.blockingExclusive);
      final receipt = _EncryptedQueueFile(
        File('$path/enrollment-receipt'),
        storageKey,
        kind: 'enrollment-receipt-v1',
      );
      final prior = await receipt.read({});
      if (prior.isEmpty) {
        if (await File('$path/activation').exists()) _reject();
        await receipt.write({'root': p['root']});
      } else if (!_anchorEqual(prior, {'root': p['root']})) {
        _reject();
      }
      await history.bootstrap({
        ...anchor,
        'registry': p['registry'],
        'collection': p['collection'],
        'opaque_b64': p['opaque_b64'],
        for (final k in [
          'current_context',
          'recipient_device_id',
          'confirmed_transcript',
        ])
          k: expected[k],
      });
      if (p['historical_authority'] != null) {
        await history.installHistoricalAuthority(
          _object(p['historical_authority']),
          collection: _object(p['collection']),
          opaqueState: bytes,
        );
      }
      final owner = AtlasVaultEpochVault(
        directory,
        storageKey: storageKey,
        deviceID: recipient.deviceId,
        registry: _epochRows(p['registry']),
        accountID: checkpoint['account_id']! as String,
        vaultID: checkpoint['vault_id']! as String,
        keyEpoch: checkpoint['key_epoch']! as int,
        stateRoot: checkpoint['state_root']! as String,
        historyOrigin: await history.publicationOrigin(),
      );
      if (await owner._file.file.exists()) {
        final state = await owner._load();
        await owner._active(state);
        if (!_anchorEqual(state['context'], owner._context)) _reject();
      } else {
        await owner.initialize(keys, history: history);
      }
      return owner;
    } catch (_) {
      _reject();
    } finally {
      private.fillRange(0, private.length, 0);
      for (final key in keys.values) {
        key.fillRange(0, key.length, 0);
      }
      if (lock != null) {
        try {
          await lock.close();
        } catch (_) {
          if (claimed) _installing.remove(path);
          _reject();
        }
      }
      if (claimed) _installing.remove(path);
    }
  }
}
