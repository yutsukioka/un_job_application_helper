import 'dart:convert';
import 'dart:typed_data';

import 'package:cryptography/cryptography.dart';

import 'crypto.dart';

final class AtlasVaultHPKEKeyDeliveryException implements Exception {
  const AtlasVaultHPKEKeyDeliveryException();

  @override
  String toString() => 'AtlasVault HPKE key delivery failed.';
}

final class AtlasVaultHPKESealedVaultKeyV2 {
  AtlasVaultHPKESealedVaultKeyV2({
    required List<int> encapsulatedKey,
    required List<int> ciphertext,
  }) : encapsulatedKey = _copyExact(encapsulatedKey, _keyLength),
       ciphertext = _copyExact(ciphertext, _ciphertextLength);

  final Uint8List encapsulatedKey;
  final Uint8List ciphertext;
}

const atlasVaultHPKEKeyDeliveryVersion = 2;
const _infoPrefix = 'atlasvault-vault-key-delivery-hpke-v2:';
const _kemId = 0x0020;
const _kdfId = 0x0001;
const _aeadId = 0x0002;
const _keyLength = 32;
const _nonceLength = 12;
const _ciphertextLength = 48;
const _maximumContextLength = 4096;

Future<AtlasVaultHPKESealedVaultKeyV2> sealAtlasVaultHPKEVaultKeyV2({
  required Uint8List recipientPublicKey,
  required Uint8List vaultKey,
  required Uint8List context,
}) async {
  try {
    return _seal(
      recipientPublicKey: recipientPublicKey,
      vaultKey: vaultKey,
      context: context,
      ephemeralKeyPair: await X25519().newKeyPair(),
    );
  } catch (_) {
    throw const AtlasVaultHPKEKeyDeliveryException();
  }
}

Future<AtlasVaultHPKESealedVaultKeyV2> sealAtlasVaultHPKEVaultKeyV2ForTesting({
  required Uint8List recipientPublicKey,
  required Uint8List vaultKey,
  required Uint8List context,
  required Uint8List ephemeralPrivateKey,
}) async {
  Uint8List? privateKey;
  try {
    privateKey = _copyExact(ephemeralPrivateKey, _keyLength);
    return _seal(
      recipientPublicKey: recipientPublicKey,
      vaultKey: vaultKey,
      context: context,
      ephemeralKeyPair: await X25519().newKeyPairFromSeed(privateKey),
    );
  } catch (_) {
    throw const AtlasVaultHPKEKeyDeliveryException();
  } finally {
    atlasVaultWipeBytesInternal(privateKey);
  }
}

Future<Uint8List> openAtlasVaultHPKEVaultKeyV2({
  required Uint8List recipientPrivateKey,
  required AtlasVaultHPKESealedVaultKeyV2 sealed,
  required Uint8List context,
}) async {
  Uint8List? privateKey;
  Uint8List? shared;
  Uint8List? key;
  Uint8List? nonce;
  Uint8List? plaintext;
  try {
    privateKey = _copyExact(recipientPrivateKey, _keyLength);
    final keyPair = await X25519().newKeyPairFromSeed(privateKey);
    final recipientPublicKey = await keyPair.extractPublicKey();
    final sharedKey = await X25519().sharedSecretKey(
      keyPair: keyPair,
      remotePublicKey: SimplePublicKey(
        sealed.encapsulatedKey,
        type: KeyPairType.x25519,
      ),
    );
    shared = Uint8List.fromList(await sharedKey.extractBytes());
    sharedKey.destroy();
    final schedule = await _keySchedule(
      dh: shared,
      encapsulatedKey: sealed.encapsulatedKey,
      recipientPublicKey: Uint8List.fromList(recipientPublicKey.bytes),
      info: _info(context),
    );
    key = schedule.key;
    nonce = schedule.nonce;
    plaintext = await atlasVaultOpenAes256GcmInternal(
      ciphertextAndTag: sealed.ciphertext,
      key: key,
      nonce: nonce,
      aad: Uint8List(0),
    );
    return Uint8List.fromList(_copyExact(plaintext, _keyLength));
  } catch (_) {
    throw const AtlasVaultHPKEKeyDeliveryException();
  } finally {
    atlasVaultWipeBytesInternal(privateKey);
    atlasVaultWipeBytesInternal(shared);
    atlasVaultWipeBytesInternal(key);
    atlasVaultWipeBytesInternal(nonce);
    atlasVaultWipeBytesInternal(plaintext);
  }
}

Future<AtlasVaultHPKESealedVaultKeyV2> _seal({
  required Uint8List recipientPublicKey,
  required Uint8List vaultKey,
  required Uint8List context,
  required SimpleKeyPair ephemeralKeyPair,
}) async {
  Uint8List? shared;
  Uint8List? key;
  Uint8List? nonce;
  Uint8List? plaintext;
  try {
    final recipient = _copyExact(recipientPublicKey, _keyLength);
    plaintext = _copyExact(vaultKey, _keyLength);
    final ephemeralPublicKey = await ephemeralKeyPair.extractPublicKey();
    final encapsulatedKey = Uint8List.fromList(ephemeralPublicKey.bytes);
    final sharedKey = await X25519().sharedSecretKey(
      keyPair: ephemeralKeyPair,
      remotePublicKey: SimplePublicKey(recipient, type: KeyPairType.x25519),
    );
    shared = Uint8List.fromList(await sharedKey.extractBytes());
    sharedKey.destroy();
    final schedule = await _keySchedule(
      dh: shared,
      encapsulatedKey: encapsulatedKey,
      recipientPublicKey: recipient,
      info: _info(context),
    );
    key = schedule.key;
    nonce = schedule.nonce;
    return AtlasVaultHPKESealedVaultKeyV2(
      encapsulatedKey: encapsulatedKey,
      ciphertext: await atlasVaultSealAes256GcmInternal(
        plaintext: plaintext,
        key: key,
        nonce: nonce,
        aad: Uint8List(0),
      ),
    );
  } catch (_) {
    throw const AtlasVaultHPKEKeyDeliveryException();
  } finally {
    atlasVaultWipeBytesInternal(shared);
    atlasVaultWipeBytesInternal(key);
    atlasVaultWipeBytesInternal(nonce);
    atlasVaultWipeBytesInternal(plaintext);
  }
}

Future<({Uint8List key, Uint8List nonce})> _keySchedule({
  required Uint8List dh,
  required Uint8List encapsulatedKey,
  required Uint8List recipientPublicKey,
  required Uint8List info,
}) async {
  if (dh.every((byte) => byte == 0)) {
    throw const AtlasVaultHPKEKeyDeliveryException();
  }
  Uint8List? eaePrk;
  Uint8List? sharedSecret;
  Uint8List? pskIdHash;
  Uint8List? infoHash;
  Uint8List? scheduleContext;
  Uint8List? secret;
  try {
    final kemSuite = Uint8List.fromList(<int>[
      ...ascii.encode('KEM'),
      ..._i2osp(_kemId, 2),
    ]);
    eaePrk = await _labeledExtract(
      suite: kemSuite,
      salt: Uint8List(0),
      label: 'eae_prk',
      inputKeyMaterial: _copyExact(dh, _keyLength),
    );
    sharedSecret = await _labeledExpand(
      suite: kemSuite,
      pseudorandomKey: eaePrk,
      label: 'shared_secret',
      info: Uint8List.fromList(<int>[
        ...encapsulatedKey,
        ...recipientPublicKey,
      ]),
      length: _keyLength,
    );
    final suite = Uint8List.fromList(<int>[
      ...ascii.encode('HPKE'),
      ..._i2osp(_kemId, 2),
      ..._i2osp(_kdfId, 2),
      ..._i2osp(_aeadId, 2),
    ]);
    pskIdHash = await _labeledExtract(
      suite: suite,
      salt: Uint8List(0),
      label: 'psk_id_hash',
      inputKeyMaterial: Uint8List(0),
    );
    infoHash = await _labeledExtract(
      suite: suite,
      salt: Uint8List(0),
      label: 'info_hash',
      inputKeyMaterial: info,
    );
    scheduleContext = Uint8List.fromList(<int>[0, ...pskIdHash, ...infoHash]);
    secret = await _labeledExtract(
      suite: suite,
      salt: sharedSecret,
      label: 'secret',
      inputKeyMaterial: Uint8List(0),
    );
    return (
      key: await _labeledExpand(
        suite: suite,
        pseudorandomKey: secret,
        label: 'key',
        info: scheduleContext,
        length: _keyLength,
      ),
      nonce: await _labeledExpand(
        suite: suite,
        pseudorandomKey: secret,
        label: 'base_nonce',
        info: scheduleContext,
        length: _nonceLength,
      ),
    );
  } finally {
    atlasVaultWipeBytesInternal(eaePrk);
    atlasVaultWipeBytesInternal(sharedSecret);
    atlasVaultWipeBytesInternal(pskIdHash);
    atlasVaultWipeBytesInternal(infoHash);
    atlasVaultWipeBytesInternal(scheduleContext);
    atlasVaultWipeBytesInternal(secret);
  }
}

Future<Uint8List> _labeledExtract({
  required Uint8List suite,
  required Uint8List salt,
  required String label,
  required Uint8List inputKeyMaterial,
}) {
  return _hkdfExtract(
    salt,
    Uint8List.fromList(<int>[
      ...ascii.encode('HPKE-v1'),
      ...suite,
      ...ascii.encode(label),
      ...inputKeyMaterial,
    ]),
  );
}

Future<Uint8List> _labeledExpand({
  required Uint8List suite,
  required Uint8List pseudorandomKey,
  required String label,
  required Uint8List info,
  required int length,
}) {
  return _hkdfExpand(
    pseudorandomKey,
    Uint8List.fromList(<int>[
      ..._i2osp(length, 2),
      ...ascii.encode('HPKE-v1'),
      ...suite,
      ...ascii.encode(label),
      ...info,
    ]),
    length,
  );
}

Future<Uint8List> _hkdfExtract(Uint8List salt, Uint8List input) {
  return _hmac(salt.isEmpty ? Uint8List(32) : salt, input);
}

Future<Uint8List> _hkdfExpand(
  Uint8List pseudorandomKey,
  Uint8List info,
  int length,
) async {
  if (length <= 0 || length > 255 * 32) {
    throw const AtlasVaultHPKEKeyDeliveryException();
  }
  final output = Uint8List(length);
  var previous = Uint8List(0);
  var offset = 0;
  try {
    for (var counter = 1; offset < length; counter += 1) {
      final hmacInput = Uint8List.fromList(<int>[
        ...previous,
        ...info,
        counter,
      ]);
      try {
        final next = await _hmac(pseudorandomKey, hmacInput);
        atlasVaultWipeBytesInternal(previous);
        previous = next;
        final remaining = length - offset;
        final count = remaining < previous.length ? remaining : previous.length;
        output.setRange(offset, offset + count, previous);
        offset += count;
      } finally {
        atlasVaultWipeBytesInternal(hmacInput);
      }
    }
    return Uint8List.fromList(output);
  } finally {
    atlasVaultWipeBytesInternal(previous);
    atlasVaultWipeBytesInternal(output);
  }
}

Future<Uint8List> _hmac(Uint8List key, Uint8List input) async {
  final secret = SecretKeyData(
    Uint8List.fromList(key),
    overwriteWhenDestroyed: true,
  );
  try {
    final mac = await Hmac.sha256().calculateMac(input, secretKey: secret);
    return Uint8List.fromList(mac.bytes);
  } finally {
    secret.destroy();
  }
}

Uint8List _info(Uint8List context) {
  if (context.isEmpty || context.length > _maximumContextLength) {
    throw const AtlasVaultHPKEKeyDeliveryException();
  }
  return Uint8List.fromList(<int>[...ascii.encode(_infoPrefix), ...context]);
}

Uint8List _i2osp(int value, int length) {
  final result = Uint8List(length);
  var remaining = value;
  for (var index = length - 1; index >= 0; index -= 1) {
    result[index] = remaining & 0xff;
    remaining >>= 8;
  }
  if (remaining != 0) {
    throw const AtlasVaultHPKEKeyDeliveryException();
  }
  return result;
}

Uint8List _copyExact(List<int> value, int length) {
  if (value.length != length) {
    throw const AtlasVaultHPKEKeyDeliveryException();
  }
  return Uint8List.fromList(value);
}
