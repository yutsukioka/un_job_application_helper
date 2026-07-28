package com.yutsukioka.jobagg.atlas

import android.content.Context
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
    context: Context,
) : AutoCloseable {
    private val applicationContext = context.applicationContext
    private val executor: ExecutorService = Executors.newSingleThreadExecutor()
    private val mainHandler = Handler(Looper.getMainLooper())
    private val masterKeyLock = Any()
    private var channel: MethodChannel? = null

    fun attach(messenger: BinaryMessenger) {
        check(channel == null)
        channel = MethodChannel(messenger, CHANNEL_NAME).also { methodChannel ->
            methodChannel.setMethodCallHandler(::handleMethodCall)
        }
    }

    override fun close() {
        channel?.setMethodCallHandler(null)
        channel = null
        executor.shutdown()
    }

    private fun handleMethodCall(call: MethodCall, result: MethodChannel.Result) {
        if (call.method !in SUPPORTED_METHODS) {
            result.notImplemented()
            return
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

    private fun decodeSha256(value: String): ByteArray {
        if (!SHA256_PATTERN.matches(value)) {
            throw StorageFailure()
        }
        return ByteArray(SHA256_BYTES) { index ->
            value.substring(index * 2, index * 2 + 2).toInt(16).toByte()
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
        const val LOCAL_STORE_FORMAT = "atlasvault-local-store"
        const val LOCAL_STORE_VERSION = 1
        const val VAULT_FORMAT = "atlas-vault"
        const val VAULT_VERSION = 1
        const val LOCAL_STORE_FILE = "atlasvault-local-store.json"
        const val VAULT_KEY_BYTES = 32
        const val NONCE_BYTES = 12
        const val WRAPPED_KEY_BYTES = 48
        const val TAG_BITS = 128
        const val SHA256_BYTES = 32
        const val MAX_KEY_ENVELOPE_BYTES = 16 * 1024
        const val MAX_STORE_BYTES = 128 * 1024 * 1024

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
