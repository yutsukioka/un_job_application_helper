library;

export 'src/atlas_vault/device_identity.dart';
export 'src/atlas_vault/crypto.dart'
    show
        AtlasVaultCryptoException,
        atlasVaultPassphraseWrapV1Aad,
        atlasVaultRecordAad,
        atlasVaultSha256Hex,
        deriveAtlasVaultPassphraseWrappingKeyV1,
        deriveAtlasVaultRecordKey,
        openAtlasVaultRecord,
        sealAtlasVaultRecord,
        unwrapAtlasVaultPassphraseWrapV1,
        wrapAtlasVaultKeyWithPassphraseV1;
export 'src/atlas_vault/export.dart';
export 'src/atlas_vault/key_delivery.dart'
    hide createAtlasVaultKeyDeliveryForTesting;
export 'src/atlas_vault/hpke_key_delivery.dart'
    hide sealAtlasVaultHPKEVaultKeyV2ForTesting;
export 'src/atlas_vault/models.dart';
export 'src/atlas_vault/payloads.dart';
export 'src/atlas_vault/pairing.dart';
export 'src/atlas_vault/pairing_transaction.dart';
export 'src/atlas_vault/pairing_view.dart';
export 'src/atlas_vault/protected_state_bounds.dart';
export 'src/atlas_vault/trusted_devices.dart';
export 'src/atlas_vault/recovery.dart'
    show
        AtlasVaultRecoveryKey,
        atlasVaultRecoveryWrapV2Aad,
        unwrapAtlasVaultExportVaultKey,
        unwrapAtlasVaultRecoveryWrapV2,
        wrapAtlasVaultKeyWithRecoveryV2;
export 'src/atlas_vault/strict_values.dart' show AtlasVaultFormatException;
