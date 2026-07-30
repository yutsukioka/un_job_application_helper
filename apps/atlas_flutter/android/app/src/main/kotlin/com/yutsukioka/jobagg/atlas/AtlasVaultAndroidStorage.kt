package com.yutsukioka.jobagg.atlas

import android.app.Activity
import android.content.Context
import android.content.Intent
import android.os.Build
import android.os.Handler
import android.os.Looper
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyInfo
import android.security.keystore.KeyProperties
import android.system.ErrnoException
import android.system.Os
import android.system.OsConstants
import android.util.AtomicFile
import android.util.Base64
import io.flutter.plugin.common.BinaryMessenger
import io.flutter.plugin.common.MethodCall
import io.flutter.plugin.common.MethodChannel
import java.io.ByteArrayOutputStream
import java.io.File
import java.io.FileNotFoundException
import java.io.FileOutputStream
import java.nio.ByteBuffer
import java.nio.charset.CodingErrorAction
import java.nio.charset.StandardCharsets
import java.security.KeyStore
import java.security.MessageDigest
import java.util.Locale
import java.util.concurrent.ExecutorService
import java.util.concurrent.Executors
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.SecretKeyFactory
import javax.crypto.spec.GCMParameterSpec
import org.json.JSONArray
import org.json.JSONObject
import org.json.JSONTokener

internal class AtlasVaultAndroidStorage(
    private val activity: Activity,
) : AutoCloseable {
    private val applicationContext: Context = activity.applicationContext
    private val executor: ExecutorService = Executors.newSingleThreadExecutor()
    private val mainHandler = Handler(Looper.getMainLooper())
    private val masterKeyLock = Any()
    private var channel: MethodChannel? = null
    private var pendingDocumentOperation: PendingDocumentOperation? = null
    private var closed = false

    fun attach(messenger: BinaryMessenger) {
        check(channel == null)
        check(!closed)
        channel = MethodChannel(messenger, CHANNEL_NAME).also { methodChannel ->
            methodChannel.setMethodCallHandler(::handleMethodCall)
        }
    }

    override fun close() {
        closed = true
        val pending = pendingDocumentOperation
        pendingDocumentOperation = null
        if (pending != null) {
            pending.bytes?.fill(0)
            pending.result.success(
                if (pending.kind == DocumentOperationKind.PICK) null else false,
            )
        }
        channel?.setMethodCallHandler(null)
        channel = null
        executor.shutdown()
    }

    private fun handleMethodCall(call: MethodCall, result: MethodChannel.Result) {
        if (call.method !in SUPPORTED_METHODS) {
            result.notImplemented()
            return
        }
        when (call.method) {
            "pickEncryptedExport" -> {
                beginPickEncryptedExport(result)
                return
            }
            "saveEncryptedExport" -> {
                beginSaveEncryptedExport(call, result)
                return
            }
        }
        executor.execute {
            try {
                val value = when (call.method) {
                    "capabilities" -> capabilities()
                    "createVaultKey" -> {
                        createVaultKey(
                            requiredVaultId(call),
                            requiredBytes(call, "vault_key"),
                        )
                        null
                    }
                    "loadVaultKey" -> loadVaultKey(requiredVaultId(call))
                    "containsVaultKey" -> containsVaultKey(requiredVaultId(call))
                    "deleteVaultKey" -> {
                        deleteVaultKey(requiredVaultId(call))
                        null
                    }
                    "readLocalStore" -> readLocalStore(requiredVaultId(call))
                    "createLocalStore" -> {
                        createLocalStore(
                            requiredVaultId(call),
                            requiredBytes(call, "store_bytes"),
                        )
                        null
                    }
                    "replaceLocalStore" -> {
                        replaceLocalStore(
                            requiredVaultId(call),
                            requiredBytes(call, "store_bytes"),
                            requiredString(call, "expected_sha256"),
                        )
                        null
                    }
                    "deleteLocalStore" -> {
                        deleteLocalStore(requiredVaultId(call))
                        null
                    }
                    "readPlaintextMigrationJournal" ->
                        readPlaintextMigrationJournal()
                    "createPlaintextMigrationJournal" -> {
                        createPlaintextMigrationJournal(
                            requiredBytes(call, "journal_bytes"),
                        )
                        null
                    }
                    "replacePlaintextMigrationJournal" -> {
                        replacePlaintextMigrationJournal(
                            requiredBytes(call, "journal_bytes"),
                            requiredString(call, "expected_sha256"),
                        )
                        null
                    }
                    "deletePlaintextMigrationJournal" -> {
                        deletePlaintextMigrationJournal(
                            requiredString(call, "expected_sha256"),
                            requiredBoolean(call, "allow_absent"),
                        )
                        null
                    }
                    "readRecoveryImportJournal" ->
                        readRecoveryImportJournal()
                    "createRecoveryImportJournal" -> {
                        createRecoveryImportJournal(
                            requiredBytes(call, "journal_bytes"),
                        )
                        null
                    }
                    "replaceRecoveryImportJournal" -> {
                        replaceRecoveryImportJournal(
                            requiredBytes(call, "journal_bytes"),
                            requiredString(call, "expected_sha256"),
                        )
                        null
                    }
                    "deleteRecoveryImportJournal" -> {
                        deleteRecoveryImportJournal(
                            requiredString(call, "expected_sha256"),
                            requiredBoolean(call, "allow_absent"),
                        )
                        null
                    }
                    "readSelectedVault" -> readSelectedVault()
                    "createSelectedVault" -> {
                        createSelectedVault(requiredVaultId(call))
                        null
                    }
                    "clearSelectedVault" -> {
                        clearSelectedVault(
                            requiredString(call, "expected_vault_id"),
                        )
                        null
                    }
                    else -> throw StorageFailure()
                }
                mainHandler.post { result.success(value) }
            } catch (_: Throwable) {
                mainHandler.post {
                    result.error(
                        ERROR_CODE,
                        "AtlasVault Android storage operation failed.",
                        null,
                    )
                }
            }
        }
    }

    fun onActivityResult(
        requestCode: Int,
        resultCode: Int,
        data: Intent?,
    ): Boolean {
        if (requestCode != SAVE_DOCUMENT_REQUEST_CODE &&
            requestCode != PICK_DOCUMENT_REQUEST_CODE
        ) {
            return false
        }
        val pending = pendingDocumentOperation ?: return true
        val expectedKind =
            if (requestCode == PICK_DOCUMENT_REQUEST_CODE) {
                DocumentOperationKind.PICK
            } else {
                DocumentOperationKind.SAVE
            }
        if (pending.kind != expectedKind) {
            pendingDocumentOperation = null
            pending.bytes?.fill(0)
            pending.result.error(
                ERROR_CODE,
                "AtlasVault Android storage operation failed.",
                null,
            )
            return true
        }
        if (resultCode != Activity.RESULT_OK || data?.data == null) {
            pendingDocumentOperation = null
            pending.bytes?.fill(0)
            pending.result.success(
                if (pending.kind == DocumentOperationKind.PICK) null else false,
            )
            return true
        }
        val uri = data.data ?: run {
            pendingDocumentOperation = null
            pending.bytes?.fill(0)
            pending.result.success(
                if (pending.kind == DocumentOperationKind.PICK) null else false,
            )
            return true
        }
        executor.execute {
            var pickedBytes: ByteArray? = null
            var succeeded = false
            try {
                if (pending.kind == DocumentOperationKind.PICK) {
                    pickedBytes = readEncryptedDocument(uri)
                } else {
                    writeEncryptedDocument(
                        uri,
                        pending.bytes ?: throw StorageFailure(),
                    )
                }
                succeeded = true
            } catch (_: Throwable) {
                succeeded = false
            } finally {
                pending.bytes?.fill(0)
                mainHandler.post {
                    if (pendingDocumentOperation === pending) {
                        pendingDocumentOperation = null
                        if (!closed) {
                            if (succeeded) {
                                if (pending.kind ==
                                    DocumentOperationKind.PICK
                                ) {
                                    val resultBytes = pickedBytes
                                    if (resultBytes == null) {
                                        pending.result.error(
                                            ERROR_CODE,
                                            "AtlasVault Android storage operation failed.",
                                            null,
                                        )
                                    } else {
                                        pending.result.success(resultBytes)
                                        resultBytes.fill(0)
                                    }
                                } else {
                                    pending.result.success(true)
                                }
                            } else {
                                pending.result.error(
                                    ERROR_CODE,
                                    "AtlasVault Android storage operation failed.",
                                    null,
                                )
                            }
                        }
                    }
                    pickedBytes?.fill(0)
                }
            }
        }
        return true
    }

    private fun beginPickEncryptedExport(result: MethodChannel.Result) {
        if (closed || pendingDocumentOperation != null) {
            result.error(
                ERROR_CODE,
                "AtlasVault Android storage operation failed.",
                null,
            )
            return
        }
        pendingDocumentOperation = PendingDocumentOperation(
            result = result,
            kind = DocumentOperationKind.PICK,
            bytes = null,
        )
        val intent = Intent(Intent.ACTION_OPEN_DOCUMENT).apply {
            addCategory(Intent.CATEGORY_OPENABLE)
            type = "*/*"
            putExtra(
                Intent.EXTRA_MIME_TYPES,
                arrayOf(
                    ENCRYPTED_EXPORT_MIME_TYPE,
                    "application/json",
                    "application/octet-stream",
                ),
            )
        }
        try {
            activity.startActivityForResult(intent, PICK_DOCUMENT_REQUEST_CODE)
        } catch (_: Throwable) {
            pendingDocumentOperation = null
            result.error(
                ERROR_CODE,
                "AtlasVault Android storage operation failed.",
                null,
            )
        }
    }

    private fun beginSaveEncryptedExport(
        call: MethodCall,
        result: MethodChannel.Result,
    ) {
        if (closed || pendingDocumentOperation != null) {
            result.error(
                ERROR_CODE,
                "AtlasVault Android storage operation failed.",
                null,
            )
            return
        }
        val supplied = try {
            requiredBytes(call, "export_bytes")
        } catch (_: Throwable) {
            result.error(
                ERROR_CODE,
                "AtlasVault Android storage operation failed.",
                null,
            )
            return
        }
        if (supplied.isEmpty() || supplied.size > MAX_DOCUMENT_BYTES) {
            supplied.fill(0)
            result.error(
                ERROR_CODE,
                "AtlasVault Android storage operation failed.",
                null,
            )
            return
        }
        val bytes = supplied.copyOf()
        supplied.fill(0)
        pendingDocumentOperation = PendingDocumentOperation(
            result = result,
            kind = DocumentOperationKind.SAVE,
            bytes = bytes,
        )
        val intent = Intent(Intent.ACTION_CREATE_DOCUMENT).apply {
            addCategory(Intent.CATEGORY_OPENABLE)
            type = ENCRYPTED_EXPORT_MIME_TYPE
            putExtra(Intent.EXTRA_TITLE, ENCRYPTED_EXPORT_FILENAME)
        }
        try {
            activity.startActivityForResult(intent, SAVE_DOCUMENT_REQUEST_CODE)
        } catch (_: Throwable) {
            pendingDocumentOperation = null
            bytes.fill(0)
            result.error(
                ERROR_CODE,
                "AtlasVault Android storage operation failed.",
                null,
            )
        }
    }

    private fun writeEncryptedDocument(uri: android.net.Uri, bytes: ByteArray) {
        if (bytes.isEmpty() || bytes.size > MAX_DOCUMENT_BYTES) {
            throw StorageFailure()
        }
        val output = applicationContext.contentResolver.openOutputStream(uri, "wt")
            ?: throw StorageFailure()
        output.use {
            it.write(bytes)
            it.flush()
        }
    }

    private fun readEncryptedDocument(uri: android.net.Uri): ByteArray {
        val input = applicationContext.contentResolver.openInputStream(uri)
            ?: throw StorageFailure()
        val output = WipeableByteArrayOutputStream()
        val buffer = ByteArray(8192)
        try {
            input.use {
                var total = 0
                while (true) {
                    val count = it.read(buffer)
                    if (count < 0) {
                        break
                    }
                    total += count
                    if (total > MAX_DOCUMENT_BYTES) {
                        throw StorageFailure()
                    }
                    output.write(buffer, 0, count)
                }
            }
            val bytes = output.toByteArray()
            if (bytes.isEmpty() || bytes.size > MAX_DOCUMENT_BYTES) {
                bytes.fill(0)
                throw StorageFailure()
            }
            return bytes
        } finally {
            buffer.fill(0)
            output.wipe()
        }
    }

    private fun capabilities(): Map<String, Any> {
        var aesGcmAvailable = false
        try {
            KeyStore.getInstance(ANDROID_KEYSTORE)
            KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, ANDROID_KEYSTORE)
            Cipher.getInstance(AES_GCM)
            aesGcmAvailable = Build.VERSION.SDK_INT >= Build.VERSION_CODES.M
        } catch (_: Exception) {
            aesGcmAvailable = false
        }

        var hardwareBacked = false
        var strongBoxBacked = false
        try {
            val keyStore = loadKeyStore()
            val key = keyStore.getKey(MASTER_KEY_ALIAS, null) as? SecretKey
            if (key != null) {
                val factory = SecretKeyFactory.getInstance(
                    key.algorithm,
                    ANDROID_KEYSTORE,
                )
                val info = factory.getKeySpec(key, KeyInfo::class.java)
                hardwareBacked =
                    KeyInfo::class.java
                        .getMethod("isInsideSecureHardware")
                        .invoke(info) as? Boolean ?: false
                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
                    val securityLevel =
                        KeyInfo::class.java
                            .getMethod("getSecurityLevel")
                            .invoke(info) as? Int
                    strongBoxBacked =
                        securityLevel == KeyProperties.SECURITY_LEVEL_STRONGBOX
                }
            }
        } catch (_: Exception) {
            hardwareBacked = false
            strongBoxBacked = false
        }

        return linkedMapOf(
            "api_level" to Build.VERSION.SDK_INT,
            "secure_boundary_available" to
                (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M && aesGcmAvailable),
            "aes_gcm_keystore_available" to aesGcmAvailable,
            "hardware_backed" to hardwareBacked,
            "strongbox_backed" to strongBoxBacked,
            "no_backup_storage_available" to
                applicationContext.noBackupFilesDir.isDirectory,
        )
    }

    private fun createVaultKey(vaultId: String, suppliedKey: ByteArray) {
        if (suppliedKey.size != VAULT_KEY_BYTES) {
            throw StorageFailure()
        }
        val vaultKey = suppliedKey.copyOf()
        try {
            val file = keyFile(vaultId, createParent = true)
            if (atomicFileExists(file, keysRoot(create = false))) {
                throw StorageFailure()
            }
            val cipher = Cipher.getInstance(AES_GCM)
            cipher.init(Cipher.ENCRYPT_MODE, getOrCreateMasterKey())
            cipher.updateAAD(keyWrapAad(vaultId))
            val ciphertext = cipher.doFinal(vaultKey)
            val nonce = cipher.iv.copyOf()
            if (nonce.size != NONCE_BYTES ||
                ciphertext.size != WRAPPED_KEY_BYTES
            ) {
                nonce.fill(0)
                ciphertext.fill(0)
                throw StorageFailure()
            }
            val envelope = linkedMapOf<String, Any>(
                "format" to KEY_WRAP_FORMAT,
                "version" to KEY_WRAP_VERSION,
                "vault_id" to vaultId,
                "nonce" to Base64.encodeToString(nonce, Base64.NO_WRAP),
                "ciphertext" to Base64.encodeToString(ciphertext, Base64.NO_WRAP),
            )
            val bytes = canonicalJsonBytes(envelope)
            try {
                atomicWrite(file, bytes, createOnly = true)
                val restored = loadVaultKey(vaultId) ?: throw StorageFailure()
                try {
                    if (!MessageDigest.isEqual(vaultKey, restored)) {
                        throw StorageFailure()
                    }
                } finally {
                    restored.fill(0)
                }
            } finally {
                bytes.fill(0)
                ciphertext.fill(0)
                nonce.fill(0)
            }
        } finally {
            vaultKey.fill(0)
            suppliedKey.fill(0)
        }
    }

    private fun loadVaultKey(vaultId: String): ByteArray? {
        val file = keyFile(vaultId, createParent = false)
        if (!atomicFileExists(file, keysRoot(create = false))) {
            return null
        }
        val envelopeBytes = readBoundedFile(file, MAX_KEY_ENVELOPE_BYTES)
        try {
            val envelope = parseStrictObject(envelopeBytes)
            requireExactKeys(
                envelope,
                setOf("format", "version", "vault_id", "nonce", "ciphertext"),
            )
            if (envelope.opt("format") != KEY_WRAP_FORMAT ||
                strictInt(envelope.opt("version")) != KEY_WRAP_VERSION ||
                envelope.opt("vault_id") != vaultId
            ) {
                throw StorageFailure()
            }
            val nonce = decodeCanonicalBase64(
                strictString(envelope.opt("nonce")),
                NONCE_BYTES,
            )
            val ciphertext = decodeCanonicalBase64(
                strictString(envelope.opt("ciphertext")),
                WRAPPED_KEY_BYTES,
            )
            try {
                val cipher = Cipher.getInstance(AES_GCM)
                cipher.init(
                    Cipher.DECRYPT_MODE,
                    existingMasterKey(),
                    GCMParameterSpec(TAG_BITS, nonce),
                )
                cipher.updateAAD(keyWrapAad(vaultId))
                return cipher.doFinal(ciphertext).also {
                    if (it.size != VAULT_KEY_BYTES) {
                        it.fill(0)
                        throw StorageFailure()
                    }
                }
            } finally {
                nonce.fill(0)
                ciphertext.fill(0)
            }
        } finally {
            envelopeBytes.fill(0)
        }
    }

    private fun containsVaultKey(vaultId: String): Boolean {
        val file = keyFile(vaultId, createParent = false)
        return atomicFileExists(file, keysRoot(create = false))
    }

    private fun deleteVaultKey(vaultId: String) {
        val file = keyFile(vaultId, createParent = false)
        val root = keysRoot(create = false)
        if (!atomicFileExists(file, root)) {
            return
        }
        ensureSafeAtomicState(file, root)
        AtomicFile(file).delete()
        if (atomicFileExists(file, root)) {
            throw StorageFailure()
        }
    }

    private fun readLocalStore(vaultId: String): ByteArray? {
        val file = localStoreFile(vaultId, createParent = false)
        if (!atomicFileExists(file, vaultDirectory(vaultId, create = false))) {
            return null
        }
        val bytes = readBoundedFile(file, MAX_STORE_BYTES)
        try {
            validateCanonicalLocalStore(bytes, vaultId)
            return bytes.copyOf()
        } finally {
            bytes.fill(0)
        }
    }

    private fun createLocalStore(vaultId: String, suppliedBytes: ByteArray) {
        val bytes = suppliedBytes.copyOf()
        try {
            validateCanonicalLocalStore(bytes, vaultId)
            val file = localStoreFile(vaultId, createParent = true)
            if (atomicFileExists(file, vaultDirectory(vaultId, create = false))) {
                throw StorageFailure()
            }
            atomicWrite(file, bytes, createOnly = true)
            verifyReadBack(file, bytes, MAX_STORE_BYTES)
        } finally {
            bytes.fill(0)
            suppliedBytes.fill(0)
        }
    }

    private fun replaceLocalStore(
        vaultId: String,
        suppliedBytes: ByteArray,
        expectedSha256: String,
    ) {
        val expectedDigest = decodeSha256(expectedSha256)
        val bytes = suppliedBytes.copyOf()
        try {
            validateCanonicalLocalStore(bytes, vaultId)
            val file = localStoreFile(vaultId, createParent = false)
            val current = readBoundedFile(file, MAX_STORE_BYTES)
            try {
                val actualDigest = MessageDigest.getInstance("SHA-256").digest(current)
                try {
                    if (!MessageDigest.isEqual(actualDigest, expectedDigest)) {
                        throw StorageFailure()
                    }
                } finally {
                    actualDigest.fill(0)
                }
            } finally {
                current.fill(0)
            }
            atomicWrite(file, bytes, createOnly = false)
            verifyReadBack(file, bytes, MAX_STORE_BYTES)
        } finally {
            expectedDigest.fill(0)
            bytes.fill(0)
            suppliedBytes.fill(0)
        }
    }

    private fun deleteLocalStore(vaultId: String) {
        val file = localStoreFile(vaultId, createParent = false)
        val root = vaultDirectory(vaultId, create = false)
        if (!atomicFileExists(file, root)) {
            return
        }
        ensureSafeAtomicState(file, root)
        AtomicFile(file).delete()
        if (atomicFileExists(file, root)) {
            throw StorageFailure()
        }
    }

    private fun readPlaintextMigrationJournal(): ByteArray? {
        val bytes = readProtectedBlob(
            migrationJournalFile(createParent = false),
            MIGRATION_JOURNAL_PURPOSE,
            MAX_MIGRATION_JOURNAL_BYTES,
        ) ?: return null
        try {
            validateCanonicalJsonDocument(bytes, MAX_MIGRATION_JOURNAL_BYTES)
            return bytes.copyOf()
        } finally {
            bytes.fill(0)
        }
    }

    private fun createPlaintextMigrationJournal(suppliedBytes: ByteArray) {
        val bytes = suppliedBytes.copyOf()
        try {
            validateCanonicalJsonDocument(bytes, MAX_MIGRATION_JOURNAL_BYTES)
            createProtectedBlob(
                migrationJournalFile(createParent = true),
                MIGRATION_JOURNAL_PURPOSE,
                bytes,
                MAX_MIGRATION_JOURNAL_BYTES,
            )
        } finally {
            bytes.fill(0)
            suppliedBytes.fill(0)
        }
    }

    private fun replacePlaintextMigrationJournal(
        suppliedBytes: ByteArray,
        expectedSha256: String,
    ) {
        val bytes = suppliedBytes.copyOf()
        try {
            validateCanonicalJsonDocument(bytes, MAX_MIGRATION_JOURNAL_BYTES)
            replaceProtectedBlob(
                migrationJournalFile(createParent = false),
                MIGRATION_JOURNAL_PURPOSE,
                bytes,
                expectedSha256,
                MAX_MIGRATION_JOURNAL_BYTES,
            )
        } finally {
            bytes.fill(0)
            suppliedBytes.fill(0)
        }
    }

    private fun deletePlaintextMigrationJournal(
        expectedSha256: String,
        allowAbsent: Boolean,
    ) {
        deleteProtectedBlob(
            migrationJournalFile(createParent = false),
            MIGRATION_JOURNAL_PURPOSE,
            expectedSha256,
            allowAbsent,
            MAX_MIGRATION_JOURNAL_BYTES,
        )
    }

    private fun readRecoveryImportJournal(): ByteArray? {
        val bytes = readProtectedBlob(
            recoveryImportJournalFile(createParent = false),
            RECOVERY_IMPORT_JOURNAL_PURPOSE,
            MAX_RECOVERY_IMPORT_JOURNAL_BYTES,
        ) ?: return null
        try {
            validateCanonicalJsonDocument(
                bytes,
                MAX_RECOVERY_IMPORT_JOURNAL_BYTES,
            )
            return bytes.copyOf()
        } finally {
            bytes.fill(0)
        }
    }

    private fun createRecoveryImportJournal(suppliedBytes: ByteArray) {
        val bytes = suppliedBytes.copyOf()
        try {
            validateCanonicalJsonDocument(
                bytes,
                MAX_RECOVERY_IMPORT_JOURNAL_BYTES,
            )
            createProtectedBlob(
                recoveryImportJournalFile(createParent = true),
                RECOVERY_IMPORT_JOURNAL_PURPOSE,
                bytes,
                MAX_RECOVERY_IMPORT_JOURNAL_BYTES,
            )
        } finally {
            bytes.fill(0)
            suppliedBytes.fill(0)
        }
    }

    private fun replaceRecoveryImportJournal(
        suppliedBytes: ByteArray,
        expectedSha256: String,
    ) {
        val bytes = suppliedBytes.copyOf()
        try {
            validateCanonicalJsonDocument(
                bytes,
                MAX_RECOVERY_IMPORT_JOURNAL_BYTES,
            )
            replaceProtectedBlob(
                recoveryImportJournalFile(createParent = false),
                RECOVERY_IMPORT_JOURNAL_PURPOSE,
                bytes,
                expectedSha256,
                MAX_RECOVERY_IMPORT_JOURNAL_BYTES,
            )
        } finally {
            bytes.fill(0)
            suppliedBytes.fill(0)
        }
    }

    private fun deleteRecoveryImportJournal(
        expectedSha256: String,
        allowAbsent: Boolean,
    ) {
        deleteProtectedBlob(
            recoveryImportJournalFile(createParent = false),
            RECOVERY_IMPORT_JOURNAL_PURPOSE,
            expectedSha256,
            allowAbsent,
            MAX_RECOVERY_IMPORT_JOURNAL_BYTES,
        )
    }

    private fun readSelectedVault(): String? {
        val bytes = readProtectedBlob(
            selectedVaultFile(createParent = false),
            SELECTED_VAULT_PURPOSE,
            MAX_SELECTED_VAULT_BYTES,
        ) ?: return null
        try {
            val value = parseStrictObject(bytes)
            requireExactKeys(value, setOf("format", "version", "vault_id"))
            val vaultId = strictString(value.opt("vault_id"))
            if (value.opt("format") != SELECTED_VAULT_FORMAT ||
                strictInt(value.opt("version")) != SELECTED_VAULT_VERSION
            ) {
                throw StorageFailure()
            }
            validateVaultId(vaultId)
            val canonical = canonicalJsonBytes(value)
            try {
                if (!MessageDigest.isEqual(bytes, canonical)) {
                    throw StorageFailure()
                }
            } finally {
                canonical.fill(0)
            }
            return vaultId
        } finally {
            bytes.fill(0)
        }
    }

    private fun createSelectedVault(vaultId: String) {
        validateVaultId(vaultId)
        val bytes = canonicalJsonBytes(
            linkedMapOf<String, Any>(
                "format" to SELECTED_VAULT_FORMAT,
                "version" to SELECTED_VAULT_VERSION,
                "vault_id" to vaultId,
            ),
        )
        try {
            createProtectedBlob(
                selectedVaultFile(createParent = true),
                SELECTED_VAULT_PURPOSE,
                bytes,
                MAX_SELECTED_VAULT_BYTES,
            )
        } finally {
            bytes.fill(0)
        }
    }

    private fun clearSelectedVault(expectedVaultId: String) {
        validateVaultId(expectedVaultId)
        val current = readSelectedVault() ?: throw StorageFailure()
        if (current != expectedVaultId) {
            throw StorageFailure()
        }
        val bytes = canonicalJsonBytes(
            linkedMapOf<String, Any>(
                "format" to SELECTED_VAULT_FORMAT,
                "version" to SELECTED_VAULT_VERSION,
                "vault_id" to current,
            ),
        )
        try {
            deleteProtectedBlob(
                selectedVaultFile(createParent = false),
                SELECTED_VAULT_PURPOSE,
                sha256Hex(bytes),
                allowAbsent = false,
                maximumPlaintextBytes = MAX_SELECTED_VAULT_BYTES,
            )
        } finally {
            bytes.fill(0)
        }
    }

    private fun validateCanonicalLocalStore(bytes: ByteArray, vaultId: String) {
        if (bytes.isEmpty() || bytes.size > MAX_STORE_BYTES) {
            throw StorageFailure()
        }
        val value = parseStrictObject(bytes)
        requireExactKeys(
            value,
            setOf(
                "format",
                "version",
                "store_id",
                "created_at",
                "updated_at",
                "vault_metadata",
                "records",
            ),
        )
        if (value.opt("format") != LOCAL_STORE_FORMAT ||
            strictInt(value.opt("version")) != LOCAL_STORE_VERSION
        ) {
            throw StorageFailure()
        }
        val metadata = value.opt("vault_metadata") as? JSONObject
            ?: throw StorageFailure()
        if (metadata.opt("format") != VAULT_FORMAT ||
            strictInt(metadata.opt("version")) != VAULT_VERSION ||
            metadata.opt("vault_id") != vaultId
        ) {
            throw StorageFailure()
        }
        val canonical = canonicalJsonBytes(value)
        try {
            if (!MessageDigest.isEqual(bytes, canonical)) {
                throw StorageFailure()
            }
        } finally {
            canonical.fill(0)
        }
    }

    private fun readProtectedBlob(
        file: File,
        purpose: String,
        maximumPlaintextBytes: Int,
    ): ByteArray? {
        if (!atomicFileExists(file, atlasVaultRoot(create = false))) {
            return null
        }
        val envelopeBytes = readBoundedFile(
            file,
            protectedEnvelopeMaximum(maximumPlaintextBytes),
        )
        try {
            val envelope = parseStrictObject(envelopeBytes)
            requireExactKeys(
                envelope,
                setOf("format", "version", "purpose", "nonce", "ciphertext"),
            )
            if (envelope.opt("format") != PROTECTED_BLOB_FORMAT ||
                strictInt(envelope.opt("version")) != PROTECTED_BLOB_VERSION ||
                envelope.opt("purpose") != purpose
            ) {
                throw StorageFailure()
            }
            val canonical = canonicalJsonBytes(envelope)
            try {
                if (!MessageDigest.isEqual(envelopeBytes, canonical)) {
                    throw StorageFailure()
                }
            } finally {
                canonical.fill(0)
            }
            val nonce = decodeCanonicalBase64(
                strictString(envelope.opt("nonce")),
                NONCE_BYTES,
            )
            val ciphertext = decodeCanonicalBase64Range(
                strictString(envelope.opt("ciphertext")),
                TAG_BYTES,
                maximumPlaintextBytes + TAG_BYTES,
            )
            try {
                val cipher = Cipher.getInstance(AES_GCM)
                cipher.init(
                    Cipher.DECRYPT_MODE,
                    existingMasterKey(),
                    GCMParameterSpec(TAG_BITS, nonce),
                )
                cipher.updateAAD(protectedBlobAad(purpose))
                return cipher.doFinal(ciphertext).also {
                    if (it.isEmpty() || it.size > maximumPlaintextBytes) {
                        it.fill(0)
                        throw StorageFailure()
                    }
                }
            } finally {
                nonce.fill(0)
                ciphertext.fill(0)
            }
        } finally {
            envelopeBytes.fill(0)
        }
    }

    private fun createProtectedBlob(
        file: File,
        purpose: String,
        plaintext: ByteArray,
        maximumPlaintextBytes: Int,
    ) {
        if (plaintext.isEmpty() || plaintext.size > maximumPlaintextBytes) {
            throw StorageFailure()
        }
        if (atomicFileExists(file, atlasVaultRoot(create = false))) {
            throw StorageFailure()
        }
        val envelopeBytes = protectBlob(plaintext, purpose)
        try {
            atomicWrite(file, envelopeBytes, createOnly = true)
            val restored = readProtectedBlob(
                file,
                purpose,
                maximumPlaintextBytes,
            ) ?: throw StorageFailure()
            try {
                if (!MessageDigest.isEqual(plaintext, restored)) {
                    throw StorageFailure()
                }
            } finally {
                restored.fill(0)
            }
        } finally {
            envelopeBytes.fill(0)
        }
    }

    private fun replaceProtectedBlob(
        file: File,
        purpose: String,
        plaintext: ByteArray,
        expectedSha256: String,
        maximumPlaintextBytes: Int,
    ) {
        if (plaintext.isEmpty() || plaintext.size > maximumPlaintextBytes) {
            throw StorageFailure()
        }
        val expectedDigest = decodeSha256(expectedSha256)
        try {
            val current = readProtectedBlob(
                file,
                purpose,
                maximumPlaintextBytes,
            ) ?: throw StorageFailure()
            try {
                val actualDigest =
                    MessageDigest.getInstance("SHA-256").digest(current)
                try {
                    if (!MessageDigest.isEqual(actualDigest, expectedDigest)) {
                        throw StorageFailure()
                    }
                } finally {
                    actualDigest.fill(0)
                }
            } finally {
                current.fill(0)
            }
        } finally {
            expectedDigest.fill(0)
        }
        val envelopeBytes = protectBlob(plaintext, purpose)
        try {
            atomicWrite(file, envelopeBytes, createOnly = false)
            val restored = readProtectedBlob(
                file,
                purpose,
                maximumPlaintextBytes,
            ) ?: throw StorageFailure()
            try {
                if (!MessageDigest.isEqual(plaintext, restored)) {
                    throw StorageFailure()
                }
            } finally {
                restored.fill(0)
            }
        } finally {
            envelopeBytes.fill(0)
        }
    }

    private fun deleteProtectedBlob(
        file: File,
        purpose: String,
        expectedSha256: String,
        allowAbsent: Boolean,
        maximumPlaintextBytes: Int,
    ) {
        val current = readProtectedBlob(
            file,
            purpose,
            maximumPlaintextBytes,
        )
        if (current == null) {
            if (allowAbsent) {
                return
            }
            throw StorageFailure()
        }
        val expectedDigest = decodeSha256(expectedSha256)
        try {
            val actualDigest = MessageDigest.getInstance("SHA-256").digest(current)
            try {
                if (!MessageDigest.isEqual(actualDigest, expectedDigest)) {
                    throw StorageFailure()
                }
            } finally {
                actualDigest.fill(0)
            }
        } finally {
            current.fill(0)
            expectedDigest.fill(0)
        }
        ensureSafeAtomicState(file, atlasVaultRoot(create = false))
        AtomicFile(file).delete()
        if (atomicFileExists(file, atlasVaultRoot(create = false))) {
            throw StorageFailure()
        }
    }

    private fun protectBlob(plaintext: ByteArray, purpose: String): ByteArray {
        val cipher = Cipher.getInstance(AES_GCM)
        cipher.init(Cipher.ENCRYPT_MODE, getOrCreateMasterKey())
        cipher.updateAAD(protectedBlobAad(purpose))
        val ciphertext = cipher.doFinal(plaintext)
        val nonce = cipher.iv.copyOf()
        if (nonce.size != NONCE_BYTES ||
            ciphertext.size != plaintext.size + TAG_BYTES
        ) {
            nonce.fill(0)
            ciphertext.fill(0)
            throw StorageFailure()
        }
        try {
            return canonicalJsonBytes(
                linkedMapOf<String, Any>(
                    "format" to PROTECTED_BLOB_FORMAT,
                    "version" to PROTECTED_BLOB_VERSION,
                    "purpose" to purpose,
                    "nonce" to Base64.encodeToString(nonce, Base64.NO_WRAP),
                    "ciphertext" to
                        Base64.encodeToString(ciphertext, Base64.NO_WRAP),
                ),
            )
        } finally {
            nonce.fill(0)
            ciphertext.fill(0)
        }
    }

    private fun validateCanonicalJsonDocument(bytes: ByteArray, maximum: Int) {
        if (bytes.isEmpty() || bytes.size > maximum) {
            throw StorageFailure()
        }
        val value = parseStrictObject(bytes)
        val canonical = canonicalJsonBytes(value)
        try {
            if (!MessageDigest.isEqual(bytes, canonical)) {
                throw StorageFailure()
            }
        } finally {
            canonical.fill(0)
        }
    }

    private fun protectedEnvelopeMaximum(maximumPlaintextBytes: Int): Int {
        val encodedMaximum =
            ((maximumPlaintextBytes + TAG_BYTES + 2) / 3) * 4
        return encodedMaximum + MAX_PROTECTED_ENVELOPE_OVERHEAD
    }

    private fun atomicWrite(file: File, bytes: ByteArray, createOnly: Boolean) {
        val root = atlasVaultRoot(create = true)
        ensureContained(file, root)
        if (createOnly && atomicFileExists(file, root)) {
            throw StorageFailure()
        }
        val atomicFile = AtomicFile(file)
        var output: FileOutputStream? = null
        try {
            output = atomicFile.startWrite()
            output.write(bytes)
            output.fd.sync()
            atomicFile.finishWrite(output)
            output = null
        } catch (_: Throwable) {
            if (output != null) {
                atomicFile.failWrite(output)
            }
            throw StorageFailure()
        }
    }

    private fun verifyReadBack(file: File, expected: ByteArray, maximum: Int) {
        val restored = readBoundedFile(file, maximum)
        try {
            if (!MessageDigest.isEqual(expected, restored)) {
                throw StorageFailure()
            }
        } finally {
            restored.fill(0)
        }
    }

    private fun readBoundedFile(file: File, maximum: Int): ByteArray {
        val root = atlasVaultRoot(create = false)
        ensureSafeAtomicState(file, root)
        val input = try {
            AtomicFile(file).openRead()
        } catch (_: FileNotFoundException) {
            throw StorageFailure()
        }
        ensureSafeExistingFile(file, root)
        val length = file.length()
        if (length <= 0 || length > maximum.toLong()) {
            input.close()
            throw StorageFailure()
        }
        val bytes = input.use {
            val output = ByteArrayOutputStream(minOf(length.toInt(), 8192))
            val buffer = ByteArray(8192)
            var total = 0
            try {
                while (true) {
                    val count = input.read(buffer)
                    if (count < 0) {
                        break
                    }
                    total += count
                    if (total > maximum) {
                        throw StorageFailure()
                    }
                    output.write(buffer, 0, count)
                }
                output.toByteArray()
            } finally {
                buffer.fill(0)
                output.reset()
            }
        }
        if (bytes.isEmpty() || bytes.size > maximum || bytes.size.toLong() != length) {
            bytes.fill(0)
            throw StorageFailure()
        }
        return bytes
    }

    private fun atomicFileExists(file: File, root: File): Boolean {
        ensureSafeAtomicState(file, root)
        return try {
            AtomicFile(file).openRead().use { }
            ensureSafeExistingFile(file, root)
            true
        } catch (_: FileNotFoundException) {
            false
        }
    }

    private fun ensureSafeAtomicState(file: File, root: File) {
        for (candidate in listOf(
            file,
            File("${file.path}.bak"),
            File("${file.path}.new"),
        )) {
            ensureContained(candidate, root)
            val status = try {
                Os.lstat(candidate.absolutePath)
            } catch (error: ErrnoException) {
                if (error.errno == OsConstants.ENOENT) {
                    continue
                }
                throw StorageFailure()
            }
            if (!OsConstants.S_ISREG(status.st_mode)) {
                throw StorageFailure()
            }
        }
    }

    private fun getOrCreateMasterKey(): SecretKey {
        synchronized(masterKeyLock) {
            val keyStore = loadKeyStore()
            val existing = keyStore.getKey(MASTER_KEY_ALIAS, null) as? SecretKey
            if (existing != null) {
                return existing
            }
            val generator = KeyGenerator.getInstance(
                KeyProperties.KEY_ALGORITHM_AES,
                ANDROID_KEYSTORE,
            )
            val specification = KeyGenParameterSpec.Builder(
                MASTER_KEY_ALIAS,
                KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT,
            )
                .setKeySize(256)
                .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
                .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
                .setRandomizedEncryptionRequired(true)
                .setUserAuthenticationRequired(false)
                .build()
            generator.init(specification)
            return generator.generateKey()
        }
    }

    private fun existingMasterKey(): SecretKey {
        return loadKeyStore().getKey(MASTER_KEY_ALIAS, null) as? SecretKey
            ?: throw StorageFailure()
    }

    private fun loadKeyStore(): KeyStore {
        return KeyStore.getInstance(ANDROID_KEYSTORE).also { it.load(null) }
    }

    private fun keyWrapAad(vaultId: String): ByteArray {
        return "$KEY_WRAP_AAD_PREFIX:$APPLICATION_ID:$vaultId"
            .toByteArray(StandardCharsets.UTF_8)
    }

    private fun protectedBlobAad(purpose: String): ByteArray {
        return "$PROTECTED_BLOB_AAD_PREFIX:$APPLICATION_ID:$purpose"
            .toByteArray(StandardCharsets.UTF_8)
    }

    private fun keyFile(vaultId: String, createParent: Boolean): File {
        validateVaultId(vaultId)
        val root = keysRoot(createParent)
        return File(root, "${vaultIdHash(vaultId)}.json").also {
            ensureContained(it, root)
        }
    }

    private fun localStoreFile(vaultId: String, createParent: Boolean): File {
        validateVaultId(vaultId)
        val root = vaultDirectory(vaultId, createParent)
        return File(root, LOCAL_STORE_FILE).also {
            ensureContained(it, root)
        }
    }

    private fun migrationJournalFile(createParent: Boolean): File {
        val root = File(atlasVaultRoot(createParent), "migrations").also {
            ensureDirectory(it, createParent)
        }
        return File(root, MIGRATION_JOURNAL_FILE).also {
            ensureContained(it, root)
        }
    }

    private fun recoveryImportJournalFile(createParent: Boolean): File {
        val root = File(atlasVaultRoot(createParent), "imports").also {
            ensureDirectory(it, createParent)
        }
        return File(root, RECOVERY_IMPORT_JOURNAL_FILE).also {
            ensureContained(it, root)
        }
    }

    private fun selectedVaultFile(createParent: Boolean): File {
        val root = File(atlasVaultRoot(createParent), "selection").also {
            ensureDirectory(it, createParent)
        }
        return File(root, SELECTED_VAULT_FILE).also {
            ensureContained(it, root)
        }
    }

    private fun keysRoot(create: Boolean): File {
        return File(atlasVaultRoot(create), "keys").also {
            ensureDirectory(it, create)
        }
    }

    private fun vaultDirectory(vaultId: String, create: Boolean): File {
        validateVaultId(vaultId)
        val vaults = File(atlasVaultRoot(create), "vaults").also {
            ensureDirectory(it, create)
        }
        return File(vaults, vaultIdHash(vaultId)).also {
            ensureDirectory(it, create)
            ensureContained(it, vaults)
        }
    }

    private fun atlasVaultRoot(create: Boolean): File {
        val noBackupRoot = applicationContext.noBackupFilesDir
        val atlasVaultDirectory = File(noBackupRoot, "atlasvault").also {
            ensureDirectory(it, create)
            ensureContained(it, noBackupRoot)
        }
        return File(atlasVaultDirectory, "v1").also {
            ensureDirectory(it, create)
            ensureContained(it, atlasVaultDirectory)
        }
    }

    private fun ensureDirectory(directory: File, create: Boolean) {
        if (create && !directory.exists() && !directory.mkdirs()) {
            throw StorageFailure()
        }
        if (directory.exists() &&
            !OsConstants.S_ISDIR(Os.lstat(directory.absolutePath).st_mode)
        ) {
            throw StorageFailure()
        }
    }

    private fun ensureSafeExistingFile(file: File, root: File) {
        ensureContained(file, root)
        if (!OsConstants.S_ISREG(Os.lstat(file.absolutePath).st_mode)) {
            throw StorageFailure()
        }
    }

    private fun ensureContained(file: File, root: File) {
        val canonicalRoot = root.canonicalFile.path
        val canonicalFile = file.canonicalFile.path
        if (canonicalFile != canonicalRoot &&
            !canonicalFile.startsWith("$canonicalRoot${File.separator}")
        ) {
            throw StorageFailure()
        }
    }

    private fun vaultIdHash(vaultId: String): String {
        return MessageDigest.getInstance("SHA-256")
            .digest(vaultId.toByteArray(StandardCharsets.UTF_8))
            .joinToString(separator = "") { byte ->
                "%02x".format(Locale.US, byte.toInt() and 0xff)
            }
    }

    private fun validateVaultId(vaultId: String) {
        if (!VAULT_ID_PATTERN.matches(vaultId) ||
            vaultId.lowercase(Locale.US) in RESERVED_VAULT_IDS
        ) {
            throw StorageFailure()
        }
    }

    private fun requiredVaultId(call: MethodCall): String {
        val value = requiredString(call, "vault_id")
        validateVaultId(value)
        return value
    }

    private fun requiredString(call: MethodCall, key: String): String {
        return call.argument<String>(key) ?: throw StorageFailure()
    }

    private fun requiredBytes(call: MethodCall, key: String): ByteArray {
        return call.argument<ByteArray>(key) ?: throw StorageFailure()
    }

    private fun requiredBoolean(call: MethodCall, key: String): Boolean {
        return call.argument<Boolean>(key) ?: throw StorageFailure()
    }

    private fun parseStrictObject(bytes: ByteArray): JSONObject {
        val text = try {
            StandardCharsets.UTF_8.newDecoder()
                .onMalformedInput(CodingErrorAction.REPORT)
                .onUnmappableCharacter(CodingErrorAction.REPORT)
                .decode(ByteBuffer.wrap(bytes))
                .toString()
        } catch (_: Exception) {
            throw StorageFailure()
        }
        val tokener = JSONTokener(text)
        val value = try {
            tokener.nextValue()
        } catch (_: Exception) {
            throw StorageFailure()
        }
        if (value !is JSONObject || tokener.nextClean().code != 0) {
            throw StorageFailure()
        }
        return value
    }

    private fun requireExactKeys(value: JSONObject, expected: Set<String>) {
        val actual = buildSet {
            val keys = value.keys()
            while (keys.hasNext()) {
                add(keys.next())
            }
        }
        if (actual != expected) {
            throw StorageFailure()
        }
    }

    private fun strictInt(value: Any?): Int {
        return when (value) {
            is Int -> value
            is Long -> {
                if (value !in Int.MIN_VALUE..Int.MAX_VALUE) {
                    throw StorageFailure()
                }
                value.toInt()
            }
            else -> throw StorageFailure()
        }
    }

    private fun strictString(value: Any?): String {
        return value as? String ?: throw StorageFailure()
    }

    private fun decodeCanonicalBase64(value: String, expectedLength: Int): ByteArray {
        if (value.any { it.isWhitespace() }) {
            throw StorageFailure()
        }
        val decoded = try {
            Base64.decode(value, Base64.NO_WRAP)
        } catch (_: IllegalArgumentException) {
            throw StorageFailure()
        }
        if (decoded.size != expectedLength ||
            Base64.encodeToString(decoded, Base64.NO_WRAP) != value
        ) {
            decoded.fill(0)
            throw StorageFailure()
        }
        return decoded
    }

    private fun decodeCanonicalBase64Range(
        value: String,
        minimumLength: Int,
        maximumLength: Int,
    ): ByteArray {
        if (value.any { it.isWhitespace() }) {
            throw StorageFailure()
        }
        val decoded = try {
            Base64.decode(value, Base64.NO_WRAP)
        } catch (_: IllegalArgumentException) {
            throw StorageFailure()
        }
        if (decoded.size !in minimumLength..maximumLength ||
            Base64.encodeToString(decoded, Base64.NO_WRAP) != value
        ) {
            decoded.fill(0)
            throw StorageFailure()
        }
        return decoded
    }

    private fun decodeSha256(value: String): ByteArray {
        if (!SHA256_PATTERN.matches(value)) {
            throw StorageFailure()
        }
        return ByteArray(SHA256_BYTES) { index ->
            value.substring(index * 2, index * 2 + 2).toInt(16).toByte()
        }
    }

    private fun sha256Hex(value: ByteArray): String {
        val digest = MessageDigest.getInstance("SHA-256").digest(value)
        try {
            return digest.joinToString(separator = "") { byte ->
                "%02x".format(Locale.US, byte.toInt() and 0xff)
            }
        } finally {
            digest.fill(0)
        }
    }

    private fun canonicalJsonBytes(value: Any): ByteArray {
        return canonicalJson(value).toByteArray(StandardCharsets.UTF_8)
    }

    private fun canonicalJson(value: Any?): String {
        return when (value) {
            null, JSONObject.NULL -> "null"
            is Boolean -> if (value) "true" else "false"
            is Byte, is Short, is Int, is Long -> value.toString()
            is String -> canonicalJsonString(value)
            is JSONObject -> {
                val keys = buildList {
                    val iterator = value.keys()
                    while (iterator.hasNext()) {
                        add(iterator.next())
                    }
                }.sorted()
                keys.joinToString(prefix = "{", postfix = "}", separator = ",") {
                    "${canonicalJsonString(it)}:${canonicalJson(value.get(it))}"
                }
            }
            is JSONArray -> {
                (0 until value.length()).joinToString(
                    prefix = "[",
                    postfix = "]",
                    separator = ",",
                ) { index -> canonicalJson(value.get(index)) }
            }
            is Map<*, *> -> {
                val keys = value.keys.map {
                    it as? String ?: throw StorageFailure()
                }.sorted()
                keys.joinToString(prefix = "{", postfix = "}", separator = ",") {
                    "${canonicalJsonString(it)}:${canonicalJson(value[it])}"
                }
            }
            is Iterable<*> -> value.joinToString(
                prefix = "[",
                postfix = "]",
                separator = ",",
            ) { canonicalJson(it) }
            else -> throw StorageFailure()
        }
    }

    private fun canonicalJsonString(value: String): String {
        val result = StringBuilder(value.length + 2)
        result.append('"')
        for (character in value) {
            when (character) {
                '"' -> result.append("\\\"")
                '\\' -> result.append("\\\\")
                '\b' -> result.append("\\b")
                '\u000c' -> result.append("\\f")
                '\n' -> result.append("\\n")
                '\r' -> result.append("\\r")
                '\t' -> result.append("\\t")
                else -> {
                    if (character.code in 0x20..0x7e) {
                        result.append(character)
                    } else {
                        result.append("\\u")
                        result.append(character.code.toString(16).padStart(4, '0'))
                    }
                }
            }
        }
        result.append('"')
        return result.toString()
    }

    private class StorageFailure : Exception()

    private class WipeableByteArrayOutputStream : ByteArrayOutputStream() {
        fun wipe() {
            buf.fill(0)
            reset()
        }
    }

    private enum class DocumentOperationKind {
        PICK,
        SAVE,
    }

    private data class PendingDocumentOperation(
        val result: MethodChannel.Result,
        val kind: DocumentOperationKind,
        val bytes: ByteArray?,
    )

    private companion object {
        const val CHANNEL_NAME = "atlas/vault_android"
        const val ERROR_CODE = "atlas_vault_android_storage_failed"
        const val APPLICATION_ID = "com.yutsukioka.jobagg.atlas"
        const val ANDROID_KEYSTORE = "AndroidKeyStore"
        const val MASTER_KEY_ALIAS =
            "com.yutsukioka.jobagg.atlas.atlasvault.master.v1"
        const val AES_GCM = "AES/GCM/NoPadding"
        const val KEY_WRAP_AAD_PREFIX = "atlasvault-android-key-wrap-v1"
        const val KEY_WRAP_FORMAT = "atlasvault-android-key-wrap"
        const val KEY_WRAP_VERSION = 1
        const val PROTECTED_BLOB_AAD_PREFIX =
            "atlasvault-android-protected-blob-v1"
        const val PROTECTED_BLOB_FORMAT = "atlasvault-android-protected-blob"
        const val PROTECTED_BLOB_VERSION = 1
        const val MIGRATION_JOURNAL_PURPOSE =
            "plaintext-private-state-migration"
        const val RECOVERY_IMPORT_JOURNAL_PURPOSE = "recovery-import"
        const val SELECTED_VAULT_PURPOSE = "selected-vault"
        const val SELECTED_VAULT_FORMAT = "atlasvault-android-selected-vault"
        const val SELECTED_VAULT_VERSION = 1
        const val LOCAL_STORE_FORMAT = "atlasvault-local-store"
        const val LOCAL_STORE_VERSION = 1
        const val VAULT_FORMAT = "atlas-vault"
        const val VAULT_VERSION = 1
        const val LOCAL_STORE_FILE = "atlasvault-local-store.json"
        const val VAULT_KEY_BYTES = 32
        const val NONCE_BYTES = 12
        const val TAG_BYTES = 16
        const val WRAPPED_KEY_BYTES = 48
        const val TAG_BITS = 128
        const val SHA256_BYTES = 32
        const val MAX_KEY_ENVELOPE_BYTES = 16 * 1024
        const val MAX_STORE_BYTES = 128 * 1024 * 1024
        const val MAX_DOCUMENT_BYTES = 128 * 1024 * 1024
        const val MAX_MIGRATION_JOURNAL_BYTES = 16 * 1024 * 1024
        const val MAX_RECOVERY_IMPORT_JOURNAL_BYTES = 64 * 1024
        const val MAX_SELECTED_VAULT_BYTES = 4 * 1024
        const val MAX_PROTECTED_ENVELOPE_OVERHEAD = 4 * 1024
        const val MIGRATION_JOURNAL_FILE = "plaintext-private-state.json.enc"
        const val RECOVERY_IMPORT_JOURNAL_FILE = "recovery-import.json.enc"
        const val SELECTED_VAULT_FILE = "selected-vault.json.enc"
        const val SAVE_DOCUMENT_REQUEST_CODE = 0x4156
        const val PICK_DOCUMENT_REQUEST_CODE = 0x4157
        const val ENCRYPTED_EXPORT_MIME_TYPE =
            "application/vnd.atlasvault+json"
        const val ENCRYPTED_EXPORT_FILENAME =
            "AtlasVault-Encrypted-Backup.atlasvault"

        val SUPPORTED_METHODS = setOf(
            "capabilities",
            "createVaultKey",
            "loadVaultKey",
            "containsVaultKey",
            "deleteVaultKey",
            "readLocalStore",
            "createLocalStore",
            "replaceLocalStore",
            "deleteLocalStore",
            "readPlaintextMigrationJournal",
            "createPlaintextMigrationJournal",
            "replacePlaintextMigrationJournal",
            "deletePlaintextMigrationJournal",
            "readRecoveryImportJournal",
            "createRecoveryImportJournal",
            "replaceRecoveryImportJournal",
            "deleteRecoveryImportJournal",
            "readSelectedVault",
            "createSelectedVault",
            "clearSelectedVault",
            "saveEncryptedExport",
            "pickEncryptedExport",
        )
        val VAULT_ID_PATTERN = Regex("^[A-Za-z0-9_-]{1,96}$")
        val SHA256_PATTERN = Regex("^[0-9a-f]{64}$")
        val RESERVED_VAULT_IDS = setOf(
            "saved_search",
            "saved_job",
            "application_note",
            "profile_snippet",
            "draft_metadata",
        )
    }
}
