from __future__ import annotations

import json
import re
from copy import deepcopy
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, FormatChecker

ROOT = Path(__file__).resolve().parents[2]
PARITY = ROOT / "contracts/sync/atlasvault_private_record_parity_v1.json"
DART = "apps/atlas_flutter/lib/"
SWIFT = "apps/apple/Sources/AtlasUI/"
INTENDED_FAMILIES = {
    "saved_search",
    "saved_job",
    "application_note",
    "profile_snippet",
    "draft_metadata",
}
PRODUCTION_HOSTS = {
    "native_apple",
    "flutter_android",
    "flutter_windows",
    "flutter_ios",
    "flutter_macos",
}

# Lexical, function-scoped architecture checks, not a Dart/Swift compiler or
# behavioral proof. Quoted text and comments cannot supply executable call edges.
_LEXEMES = re.compile(
    r'//[^\n]*|/\*.*?\*/|""".*?"""|\'\'\'.*?\'\'\'|'
    r'"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'|[A-Za-z_$][\w$]*|[^\s]',
    re.DOTALL,
)


def _tokens(source: str) -> list[str]:
    return [
        m.group()
        for m in _LEXEMES.finditer(source)
        if not m.group().startswith(("//", "/*"))
    ]


def _source(path: str) -> list[str]:
    return _tokens((ROOT / path).read_text(encoding="utf-8"))


def _find(source: list[str], phrase: str, start: int = 0) -> int:
    expected = _tokens(phrase)
    # Dart formatting may add a trailing call comma. Preserve every argument
    # token, including nested expressions, while accepting that final delimiter.
    variants = [expected]
    if expected[-1:] == [")"]:
        variants.append([*expected[:-1], ",", ")"])
    for index in range(start, len(source) - len(expected) + 1):
        if any(source[index : index + len(tokens)] == tokens for tokens in variants):
            return index
    raise AssertionError(f"Missing executable source edge: {phrase}")


def _end(source: list[str], start: int, opening: str, closing: str) -> int:
    depth = 0
    for index in range(start, len(source)):
        if source[index] == opening:
            depth += 1
        elif source[index] == closing:
            depth -= 1
            if depth == 0:
                return index + 1
    raise AssertionError(f"Unbalanced source scope: {opening}")


def _block(source: list[str], anchor: str) -> list[str]:
    start = _find(source, anchor)
    opening = source.index("{", start + len(_tokens(anchor)))
    return source[start : _end(source, opening, "{", "}")]


def _function(path: str, declaration: str) -> list[str]:
    source = _source(path)
    start = _find(source, declaration)
    parameters = source.index("(", start)
    after_parameters = _end(source, parameters, "(", ")")
    # Named Dart parameters contain braces. Find the body after all parameters;
    # retain arrow/callback ownership as well as ordinary function bodies.
    for index in range(after_parameters, len(source)):
        if source[index] == "{":
            return source[start : _end(source, index, "{", "}")]
        if source[index] == ";":
            return source[start : index + 1]
    raise AssertionError(f"Missing function body: {declaration}")


def _ordered(source: list[str], *edges: str) -> None:
    position = 0
    for edge in edges:
        position = _find(source, edge, position) + len(_tokens(edge))


def _no_calls(source: list[str], *names: str) -> None:
    for name in names:
        pattern = _tokens(f"{name}(")
        assert not any(
            source[i : i + len(pattern)] == pattern for i in range(len(source))
        ), f"Unexpected call: {name}"


def test_source_edges_ignore_comments_and_string_markers() -> None:
    decoys = _tokens(
        '// owner.commit(value)\n"owner.commit(value)"; other.commit(value);'
    )
    with pytest.raises(AssertionError, match="Missing executable source edge"):
        _ordered(decoys, "owner.commit(value)")
    _ordered(_tokens("owner /* comment */ . commit ( value );"), "owner.commit(value)")
    _ordered(_tokens("owner.commit(value,);"), "owner.commit(value)")
    with pytest.raises(AssertionError, match="Missing executable source edge"):
        _ordered(_tokens("owner.commit(value + other,);"), "owner.commit(value)")
    with pytest.raises(AssertionError, match="Missing executable source edge"):
        _ordered(
            _tokens("publish(s); outbox.enqueue(op);"),
            "outbox.enqueue(op)",
            "publish(s)",
        )


def test_private_record_parity_contract_is_complete_and_qualified() -> None:
    contract = json.loads(PARITY.read_text(encoding="utf-8"))
    assert contract["format"] == "atlasvault-private-record-parity"
    assert contract["version"] == 1
    assert {
        entry["record_type"] for entry in contract["record_families"]
    } == INTENDED_FAMILIES
    assert contract["excluded_record_families"] == [
        {"record_type": "saved_text", "reason": "legacy_reference_fixture_only"},
    ]
    for entry in contract["record_families"]:
        assert set(entry["hosts"]) == PRODUCTION_HOSTS | {"python_core"}
        for host in entry["hosts"].values():
            assert set(host) == {
                "create",
                "read_list",
                "update",
                "delete_tombstone",
                "encrypted_persistence",
                "sync_encoding_decoding",
                "conflict_handling",
                "recovery_display",
            }
            assert all(type(value) is bool for value in host.values())
    assert set(contract["production_runtime_integration"]) == PRODUCTION_HOSTS
    admission = contract["runtime_admission"]
    assert admission["requires_provisioned_authenticated_owner"] is True
    assert admission["fabricated_bootstrap_allowed"] is False
    assert admission["required_legacy_context_outcome"] == "migrationRequired"
    migration = admission["legacy_import"]
    assert migration["requires_existing_secure_vault_key_and_runtime_binding"] is True
    assert migration["preserved_fields"] == [
        "id",
        "revision",
        "parent_revision",
        "tombstone",
        "payload",
    ]
    assert (
        migration["original_revision_replay_requires_exact_content_and_lineage"] is True
    )
    assert migration["unknown_revision_for_existing_id"] == "migrationRequired"
    assert migration["rewrites_legacy_source"] is False
    evidence = contract["verification"]
    assert evidence["initial_policy_red"] == {
        "passed": 3,
        "failed": 1,
        "provenance": "parent_rerun",
    }
    assert evidence["c32_full_device_vm_proof"] is False
    for report in evidence["reported_runtime_results"].values():
        assert (ROOT / report["test_path"]).is_file()
        if report.get("status") == "reported_passed":
            assert report["provenance"] == "parent_reported"
    assert set(evidence["runtime_behavior"]) == PRODUCTION_HOSTS
    for host, integrated in contract["production_runtime_integration"].items():
        assert type(integrated) is bool
        verification = evidence["runtime_behavior"][host]
        assert verification["test_paths"]
        for path in verification["test_paths"]:
            assert (ROOT / path).is_file(), f"Missing cited test: {path}"
        assert set(verification["blocking_gaps"]) <= set(evidence["blocking_gaps"])
        if integrated:
            assert verification["status"] == "verified"
            assert verification["blocking_gaps"] == []
        else:
            assert verification["status"] != "verified" or verification["blocking_gaps"]


@pytest.mark.parametrize(
    "platform,key_type,store_type",
    [
        ("windows", "AtlasWindowsVaultSecureKeyStore", "AtlasWindowsVaultLocalStoreIO"),
        ("apple", "AtlasAppleVaultSecureKeyStore", "AtlasAppleVaultLocalStoreIO"),
        ("android", "AtlasAndroidVaultSecureKeyStore", "AtlasAndroidVaultLocalStoreIO"),
    ],
)
def test_flutter_production_injects_protected_owner_factory(
    platform, key_type, store_type
) -> None:
    assembly = _function(
        DART + "features/app_shell/atlas_app.dart",
        "_AtlasDefaultControllerAssembly _buildDefaultControllerAssembly",
    )
    if platform == "windows":
        branch = _block(assembly, "if (Platform.isWindows)")
    elif platform == "apple":
        branch = _block(assembly, "if (Platform.isIOS || Platform.isMacOS)")
    else:
        branch = assembly[_find(assembly, f"final keyStore = {key_type}()") :]
    _ordered(
        branch,
        f"final keyStore = {key_type}()",
        f"final localStore = {store_type}()",
        "final runtime = AtlasVaultPrivateStateRuntime(",
        "secureKeyStore: keyStore",
        "localStoreIO: localStore",
        "epochSessionFactory: (id) => _openProductionEpochSession(keyStore, id)",
        "AtlasAppController(",
        "localCacheStoreFactory: _noPersistentPlaintextCache",
        "requireEncryptedPrivateState: true",
        "privateStatePersistence: runtime",
    )
    _no_calls(branch, "AtlasLocalCacheStore", "resolveAtlasLegacyTemporaryCacheFile")
    opener = _function(
        DART + "features/app_shell/atlas_app.dart",
        "Future<sync.AtlasVaultRuntimeSession> _openProductionEpochSession",
    )
    _ordered(opener, "_productionEpochBinding(keyStore)", ".open(vaultID)")
    binding = _function(
        DART + "features/app_shell/atlas_app.dart",
        "Future<sync.AtlasVaultRuntimeBinding> _productionEpochBinding",
    )
    _ordered(
        binding,
        "getApplicationSupportDirectory()",
        "sync.AtlasVaultRuntimeBinding(",
        "loadKey: keyStore.loadVaultKey",
        "createKey: keyStore.createVaultKey",
    )
    _no_calls(opener, "initialize", "provision", "createCommitment")
    _no_calls(binding, "initialize", "provision", "createCommitment")


def test_dart_binding_reopens_authenticated_context_without_enrollment() -> None:
    opener = _function(
        DART + "src/atlas_vault/runtime_binding.dart",
        "Future<AtlasVaultRuntimeSession> open",
    )
    _ordered(
        opener,
        "loadKey(_slot(vaultID, 'binding'))",
        "loadKey(_slot(vaultID, 'storage'))",
        "loadKey(_slot(vaultID, 'signing'))",
        "_EncryptedQueueFile(",
        "_exact(record,",
        "c['vault_id'] != vaultID",
        "AtlasVaultRevocation.registryRoot(",
        "final owner = AtlasVaultEpochVault(",
        "deviceID: c['device_id']",
        "accountID: c['account_id']",
        "stateRoot: c['state_root']",
        "owner._load()",
        "_checkSigner(owner, seed!",
        "AtlasVaultRuntimeSession._(owner,",
    )
    _no_calls(opener, "initialize", "provision", "createKey", "createCommitment")
    signer = _function(
        DART + "src/atlas_vault/runtime_binding.dart", "Future<void> _checkSigner"
    )
    _ordered(
        signer,
        "newKeyPairFromSeed(seed)",
        "_epochRows(s['registry'])",
        "d['state'] == 'ACTIVE'",
        "d['signing_public_b64'] == public",
    )
    provision = _function(
        DART + "src/atlas_vault/runtime_binding.dart", "Future<void> provision"
    )
    _ordered(
        provision,
        "owner._active(s)",
        "_checkSigner(owner, signingSeed, s)",
        "owner._publicationRegistry(s, authenticatedRegistry)",
        "history._bridge(await history._load())",
        "? publicationRegistry : null",
        "createKey(_slot(vaultID, 'storage'), owner._key)",
        "'history_registry': ?originalRegistry",
    )
    _ordered(
        opener,
        "owner._runtimePublicationRegistry = _epochRows(record['history_registry'])",
        "owner._publicationRegistry(s, null)",
        "return AtlasVaultRuntimeSession._(",
    )
    registry = _function(
        DART + "src/atlas_vault/epoch_vault.dart",
        "Future<List<Map<String, Object?>>> _publicationRegistry",
    )
    original = _block(registry, "if (bridges.isEmpty)")
    _ordered(
        original,
        "final registry = supplied ?? _runtimePublicationRegistry",
        "if (registry == null) _epochFail('ATLAS_RUNTIME_PROVISIONING_REQUIRED')",
        "prior = (await history.exportEvidence()).last",
        "AtlasVaultAuthenticatedStateView.registryRoot(registry) != prior['registry_root']",
        "_epochFail('ATLAS_RUNTIME_BINDING_REJECTED')",
        "return _epochRows(jsonDecode(jsonEncode(registry)))",
    )
    _ordered(
        registry,
        "final registry = _epochRows(s['registry'])",
        "AtlasVaultRevocation.registryRoot(supplied)",
        "AtlasVaultRevocation.registryRoot(registry)",
    )
    publication = _function(
        DART + "src/atlas_vault/runtime_records.dart",
        "Future<Map<String, Object?>> runtimePublication",
    )
    _ordered(
        publication,
        "_load()",
        "_active(s)",
        "_createCommitment(",
        "signingKey: signingKey",
        "authenticatedRegistry: authenticatedRegistry",
    )
    commitment = _function(
        DART + "src/atlas_vault/epoch_vault.dart",
        "Future<Map<String, Object?>> _createCommitment",
    )
    _ordered(
        commitment,
        "_active(s)",
        "if (s['journal'] != null &&",
        "_publicationRegistry(s, authenticatedRegistry)",
        "AtlasVaultSignedStateCommitment.sign(",
        "AtlasVaultAuthenticatedStateView.sign(",
        "history.ingest(view, publicationRegistry,",
    )


def test_dart_private_mutations_reach_one_fenced_epoch_publication() -> None:
    path = DART + "src/atlas_vault/private_state_runtime.dart"
    activation = _block(
        _function(
            path,
            "Future<AtlasVaultActivationResult> activateExisting(String vaultId) async",
        ),
        "if (_epochSessionFactory != null)",
    )
    _ordered(
        activation,
        "_epochSessionFactory(vaultId)",
        "_epochSession = candidate",
        "_localStoreIO.read(vaultId)",
        "_secureKeyStore.loadVaultKey(vaultId)",
        "AtlasVaultActivationResult.migrationRequired",
        "candidate.importLegacy(store: legacy, vaultKey: candidateKey)",
        "_readEpochSnapshot()",
        "_snapshot = projected",
        "return AtlasVaultActivationResult.activated",
    )
    _no_calls(activation, "initialize", "provision", "createRecord")
    for method in ("createRecord", "updateRecord", "deleteRecord"):
        body = _function(path, f"Future<AtlasVaultPrivateStateSnapshot> {method}")
        branch = _block(body, "if (_epochSession != null)")
        _find(branch, "return _enqueueEpochMutation(")
        _find(
            branch,
            "_commitEpochPayload("
            if method == "createRecord"
            else "_commitEpochRecord(",
        )
        _no_calls(branch, "_commitMutation")
    _find(
        _function(path, "Future<AtlasVaultPrivateStateSnapshot> _commitEpochPayload"),
        "_commitEpochRecord(envelope:",
    )
    commit = _function(
        path, "Future<AtlasVaultPrivateStateSnapshot> _commitEpochRecord"
    )
    _ordered(
        commit,
        "_requireActive()",
        "session = _epochSession",
        "session.commit(payload: envelope, objectID: recordId, expectedRevision: expectedRevision)",
        "_readEpochSnapshot()",
    )
    session = _function(
        DART + "src/atlas_vault/runtime_binding.dart",
        "Future<AtlasVaultEncryptedPatchOperation> commit",
    )
    _ordered(
        session,
        "newKeyPairFromSeed(_seed)",
        "owner.commitRuntimeRecord(payload: payload, objectID: objectID,",
        "expectedRevision: expectedRevision, signingKey: signer",
    )
    records = DART + "src/atlas_vault/runtime_records.dart"
    _find(
        _function(
            records, "Future<AtlasVaultEncryptedPatchOperation> commitRuntimeRecord"
        ),
        "commitRuntimeRecordForTesting(payload: payload, objectID: objectID,",
    )
    commit = _function(
        records,
        "Future<AtlasVaultEncryptedPatchOperation> commitRuntimeRecordForTesting",
    )
    _ordered(
        commit,
        "_run(",
        "final s = await _load()",
        "_active(s)",
        "_stageRuntimeRecord(s, payload: payload, objectID: objectID,",
        "_file.write(s,",
    )
    staging = _function(
        records, "Future<AtlasVaultEncryptedPatchOperation> _stageRuntimeRecord"
    )
    _ordered(
        staging,
        "_replica(s)",
        "_seal(s, 'patch', body",
        "_runtimeRecord(s, op)",
        "replica.ingestRemote(op)",
        "_RuntimeStagedFile(this, s, 'outbox')",
        "outbox.enqueue(op)",
    )
    _no_calls(staging, "write")
    staged = _function(
        DART + "src/atlas_vault/runtime_records.dart", "Future<void> write"
    )
    _find(staged, "state['components']")
    _no_calls(staged, "writeAsBytes", "writeAsString")
    _find(
        _function(
            DART + "src/atlas_vault/runtime_records.dart",
            "AtlasVaultDurableEncryptedConvergentReplica _replica",
        ),
        "_RuntimeStagedFile(this, s, 'runtime')",
    )


def test_swift_production_host_loads_binding_and_routes_mutations() -> None:
    harness_path = SWIFT + "AtlasVaultProductionCompositionHarness.swift"
    mac = _function(SWIFT + "AtlasMacAppProcessOwner.swift", "public convenience init")
    _find(mac, "AtlasVaultProductionCompositionFactory.makeUnwiredProductionLike(")
    harness = _function(harness_path, "static func makeUnwiredProductionLike<")
    _ordered(
        harness,
        "AtlasVaultRuntimeFactory.production(",
        "AtlasVaultRuntimeFacade.runtimeServices(runtimeServices)",
        "AtlasVaultSavedSearchCoordinator(",
        "privateMutationHost.applyPrivateMutation(request)",
        "AtlasVaultRecordsOwner(",
        "privateMutationHost.applyPrivateMutation($0)",
        "privateSessionBridge.attach(AtlasVaultCombinedPrivateBoundary(",
    )
    production = _function(
        SWIFT + "AtlasVaultRuntimeComposition.swift", "public static func production<"
    )
    _ordered(
        production,
        "makeServices(",
        "AtlasKeychainVaultKeyStore(client: keychainClient)",
        "runtimeBindingLoader:",
        "AtlasKeychainRuntimeBindingStore(client: keychainClient).load(for: vaultID)",
    )
    host = _function(
        SWIFT + "AtlasVaultProductionHost.swift", "public func applyPrivateMutation"
    )
    _ordered(
        host,
        "privateMutationAdmissionPermitted(for: request)",
        "let runtime = dependencies.runtime",
        "runtime.apply(request)",
    )
    facade = _function(SWIFT + "AtlasVaultRuntimeFacade.swift", "public func apply")
    _ordered(
        facade,
        "let mutations = request.mutations",
        "let expectedVaultID = request.expectedVaultID",
        "environment.save(mutations, expectedVaultID)",
    )
    environment = _function(
        SWIFT + "AtlasVaultRuntimeFacade.swift", "init(activationController:"
    )
    _find(
        environment,
        "activationController.saveRuntimeMutations(mutations, expectedVaultID: expectedVaultID)",
    )
    scope = _function(
        SWIFT + "AtlasVaultActivationController.swift", "static func runtimeServices<"
    )
    _ordered(
        scope,
        "services.runtimeBindingLoader",
        "let binding = try loader(vaultID)",
        "binding.open(directory: directory, session: session).runtimeState()",
        "saveMutations:",
        "binding.open(directory: directory, session: session)",
        "epoch.commitRuntimeMutations(mutations, signingKey: binding.signingKey())",
    )
    _no_calls(scope, "initialize", "createAuthenticatedBinding")
    opener = _function(SWIFT + "AtlasVaultRuntimeBinding.swift", "func open")
    _ordered(
        opener,
        "session.vaultID",
        "AtlasVaultEpochVault(",
        "epoch.load()",
        "epoch.history(s).load()",
        'value["history_context"]',
        'c["state_root"]',
        "return epoch",
    )
    _no_calls(opener, "initialize", "createAuthenticatedBinding", "generate")
    save = _function(
        SWIFT + "AtlasVaultActivationController.swift", "func saveRuntimeMutations"
    )
    _find(save, "installedSession.scope.save(mutations: mutations, session: session)")


def test_swift_record_projection_and_outbox_share_fenced_publication() -> None:
    path = SWIFT + "AtlasVaultEpochRuntime.swift"
    _find(
        _function(path, "public func commitRuntimeMutations"),
        "commitRuntimeMutationsForTesting(mutations, signingKey: signingKey)",
    )
    commit = _function(path, "func commitRuntimeMutationsForTesting")
    _ordered(
        commit,
        "try run",
        "var s = try load()",
        "try active(s)",
        "checkedRuntime(s)",
        'outbox.store = try staged.file("outbox", owner: self)',
        "try makeRuntimeOperation(",
        "replica.ingestRemote(operation)",
        "outbox.enqueue(operation)",
        's["components"] = staged.values',
        "publishRuntime(s,",
    )
    _ordered(
        _function(path, "private func makeRuntimeOperation"),
        "try seal(",
        "AtlasVaultEncryptedPatchOperation(",
        "try runtimeBody(operation)",
        "return operation",
    )
    _find(
        _function(path, "private func runtimeReplica"),
        'replica.store = try staged.file("runtime", owner: self)',
    )
    staged = _function(path, "func file")
    _find(staged, "self.values[name] = value")
    _no_calls(staged, "write", "writeAsBytes")
    _find(_function(path, "private func publishRuntime"), "file.write(s,")
    binding = _function(
        SWIFT + "AtlasVaultRuntimeBinding.swift",
        "public func createAuthenticatedBinding",
    )
    _ordered(
        binding,
        "authenticatedBindingData(",
        "client.add(.init(service: Self.storageKeyService,",
    )
    binding_data = _function(
        SWIFT + "AtlasVaultRuntimeBinding.swift",
        "private func authenticatedBindingData",
    )
    _ordered(
        binding_data,
        "epoch.active(s)",
        "let history = try epoch.history(s).load()",
        "if try EpochCatchUp.records(history).isEmpty",
        "guard let authenticatedHistoryRegistry",
        "AtlasVaultAuthenticatedStateView.registryRoot(authenticatedHistoryRegistry)",
        '== epoch.rows(history["views"]).last?["registry_root"]',
        'value["history_registry"] = authenticatedHistoryRegistry',
    )
    _ordered(
        _function(path, "public func runtimePublication"),
        "load()",
        "active(s)",
        "h.runtimeSigningPublicKey()",
        'h.store = try staged.file("history", owner: self)',
        "stageCommitment(",
        "authenticatedRegistry: authenticatedRegistry",
        "publishRuntime(s)",
    )
    commitment = _function(SWIFT + "AtlasVaultEpochVault.swift", "func stageCommitment")
    _ordered(
        commitment,
        'if let journal = s["journal"] as? [String: Any]',
        "let prior = try h.exportEvidence().last!",
        "h.runtimeSigningPublicKey()",
        "if try EpochCatchUp.records(h.load()).isEmpty",
        "guard let original = authenticatedRegistry ?? runtimeHistoryRegistry",
        "publicationRegistry = original",
        "AtlasVaultAuthenticatedStateView.registryRoot(publicationRegistry)",
        'guard registryRoot == prior["registry_root"]',
        'publicationRegistry = try rows(s["registry"])',
        "AtlasVaultRevocation.registryRoot(publicationRegistry)",
        "AtlasVaultSignedStateCommitment.sign(",
        "AtlasVaultAuthenticatedStateView.sign(",
        "h.ingest(view: view, registry: publicationRegistry,",
    )


@pytest.mark.parametrize("language", ["dart", "swift"])
def test_epoch_read_write_and_ingress_all_call_admission(language) -> None:
    if language == "dart":
        path = DART + "src/atlas_vault/runtime_records.dart"
        methods = [
            "Future<List<AtlasVaultRuntimeRecord>> runtimeRecords",
            "Future<AtlasVaultEncryptedPatchOperation> commitRuntimeRecordForTesting",
            "Future<int> ingestRuntimePage",
        ]
        for method in methods:
            _ordered(_function(path, method), "_load()", "_active(s)")
        guard = _function(
            DART + "src/atlas_vault/epoch_vault.dart", "Future<void> _active"
        )
        for state in (
            "CATCH_UP_PENDING",
            "CLEANUP_PENDING",
            "ACTIVATION_PENDING",
            "REVOKED",
        ):
            _find(guard, repr(state))
        _ordered(
            guard,
            "s['status'] != 'ACTIVE'",
            "_history(s).recovery()",
            "_epochFail('ATLAS_RECOVERY_PENDING')",
        )
    else:
        path = SWIFT + "AtlasVaultEpochRuntime.swift"
        for method in (
            "public func runtimeState",
            "func commitRuntimeMutationsForTesting",
            "public func ingestRuntimePage",
        ):
            _ordered(_function(path, method), "load()", "active(s)")
        guard = _function(SWIFT + "AtlasVaultEpochVault.swift", "func active")
        for state in (
            "CATCH_UP_PENDING",
            "CLEANUP_PENDING",
            "ACTIVATION_PENDING",
            "REVOKED",
        ):
            _find(guard, json.dumps(state))
        _ordered(
            guard,
            's["status"] as? String == "ACTIVE"',
            "history(s).recovery()",
            "throw AtlasVaultRotationError.recovery",
        )


def test_legacy_import_uses_existing_authority_and_checks_content_and_lineage() -> None:
    session = _function(
        DART + "src/atlas_vault/runtime_binding.dart", "Future<int> importLegacy"
    )
    _ordered(
        session,
        "newKeyPairFromSeed(_seed)",
        "owner.importLegacyRuntime(store: store, vaultKey: vaultKey, signingKey: signer)",
    )
    importer = _function(
        DART + "src/atlas_vault/runtime_records.dart", "Future<int> importLegacyRuntime"
    )
    _ordered(
        importer,
        "_load()",
        "_active(s)",
        "store.vaultMetadata.vaultId != _context['vault_id']",
        "record_crypto.openAtlasVaultRecord(",
        "_replica(s)._load()",
        "op.envelope.objectId == record.id",
        "op.envelope.revision == record.revision",
        "found.payload != payload",
        "found.operation.envelope.parentRevision != record.parentRevision",
        "found.operation.envelope.tombstone != record.deleted",
        "_epochFail('ATLAS_RUNTIME_MIGRATION_REQUIRED')",
        "continue",
        "if (prior.isNotEmpty) _epochFail('ATLAS_RUNTIME_MIGRATION_REQUIRED')",
        "_stageRuntimeRecord(s, payload: payload, objectID: record.id,",
        "revision: record.revision, parentRevision: record.parentRevision, signingKey: signingKey",
        "if (count > 0) await _file.write(s)",
    )
    _no_calls(
        importer, "initialize", "provision", "createKey", "store.write", "store.replace"
    )
    scope = _function(
        SWIFT + "AtlasVaultActivationController.swift", "static func runtimeServices<"
    )
    legacy_scope = _block(
        scope, "if try perVaultServices.pathLocator.localStoreExists(vaultID: vaultID)"
    )
    _find(
        _function(SWIFT + "AtlasVaultPathLocator.swift", "func localStoreExists"),
        "FileManager.default.fileExists(atPath: localStoreURL(vaultID: vaultID).path)",
    )
    _ordered(
        legacy_scope,
        "let binding = try loader(vaultID)",
        "let epoch = try binding.open(directory: directory, session: session)",
        "let legacy = try perVaultServices.localStoreIO.read(from: legacyURL)",
        "epoch.importLegacyRuntime(legacy, session: session, signingKey: binding.signingKey())",
    )
    _no_calls(legacy_scope, "write", "saveEncryptedStoreAtomically", "initialize")
    _find(
        _function(
            SWIFT + "AtlasVaultEpochRuntime.swift", "public func importLegacyRuntime"
        ),
        "importLegacyRuntimeForTesting(legacy, session: session, signingKey: signingKey)",
    )
    native = _function(
        SWIFT + "AtlasVaultEpochRuntime.swift", "func importLegacyRuntimeForTesting"
    )
    _ordered(
        native,
        "load()",
        "active(s)",
        'session.vaultID == context["vault_id"]',
        "runtimeOperations(staged.values)",
        "AtlasVaultRecordCrypto.open(",
        "$0.envelope.objectID == record.id",
        "$0.envelope.revision == record.revision",
        "operation.envelope.parentRevision == record.parentRevision",
        "operation.envelope.tombstone == record.deleted",
        'body["payload"]',
        "throw AtlasVaultActivationFailure.migrationRequired",
        "continue",
        "guard sameID.isEmpty else { throw AtlasVaultActivationFailure.migrationRequired }",
        "makeRuntimeOperation(id: record.id, revision: record.revision,",
        "parent: record.parentRevision, payload: payload",
        "replica.ingestRemote(operation)",
        "outbox.enqueue(operation)",
        "publishRuntime(s,",
    )
    _no_calls(
        native,
        "initialize",
        "createAuthenticatedBinding",
        "legacy.write",
        "legacy.replace",
    )


def test_five_family_ui_actions_reach_runtime_controllers() -> None:
    ui = DART + "features/app_shell/atlas_private_records_panel.dart"
    rendered = _function(ui, "Widget build")
    _ordered(
        rendered,
        "widget.records",
        "record.envelope.type == _family",
        "AtlasVaultPayloadType.values",
        "_open(_RecordView.create)",
        "_RecordForm(",
        "onSave: _save",
    )
    _ordered(
        _function(ui, "Future<void> _save"),
        "widget.onCreate(envelope)",
        "widget.onUpdate(record, envelope)",
    )
    _find(_function(ui, "Future<void> _delete"), "widget.onDelete(record)")
    form = _function(ui, "void _save")
    for family in (
        "savedSearch",
        "savedJob",
        "applicationNote",
        "profileSnippet",
        "draftMetadata",
    ):
        _find(form, f"case AtlasVaultPayloadType.{family}:")
    _ordered(form, "AtlasVaultPayloadEnvelope.fromJson(", "widget.onSave(envelope)")
    app = DART + "features/app_shell/atlas_app.dart"
    panel = _block(_source(app), "class AtlasSavedPanel")
    _ordered(
        panel,
        "AtlasPrivateRecordsPanel(",
        "records: controller.privateRecords",
        "enabled: controller.canMutatePrivateRecords",
        "onCreate: controller.createPrivateRecord",
        "onUpdate: controller.updatePrivateRecord",
        "onDelete: controller.deletePrivateRecord",
    )
    for action, target in (
        ("create", "createRecord"),
        ("update", "updateRecord"),
        ("delete", "deleteRecord"),
    ):
        method = _function(app, f"Future<void> {action}PrivateRecord")
        _ordered(method, "_mutatePrivateRecord(", f"runtime.{target}(")
    _ordered(
        _function(app, "Future<void> _mutatePrivateRecord"),
        "canMutatePrivateRecords",
        "action(runtime)",
        "_mayAcceptPrivateRead(runtime, generation)",
        "_installPrivateSnapshot(snapshot)",
    )
    _find(_function(app, "void _installPrivateSnapshot"), "snapshot.records")
    native = _source(SWIFT + "AtlasVaultProductionRootView.swift")
    _find(native, "AtlasVaultRecordsView(owner: recordsContext.owner)")
    _find(native, "AtlasVaultSavedSearchView(")
    records = SWIFT + "AtlasVaultRecordsView.swift"
    view = _block(_source(records), "struct AtlasVaultRecordsView")
    _ordered(
        view,
        "ForEach(AtlasVaultRecordFamily.allCases)",
        "ForEach(owner.records.filter",
        "AtlasVaultRecordDraft(family: family)",
        "AtlasVaultRecordForm(draft: value)",
        "await owner.save(value)",
        "await owner.delete(value)",
    )
    native_form = _block(_source(records), "struct AtlasVaultRecordForm")
    _ordered(
        native_form, "ForEach(draft.family.fields", "TextField(", "await save(value)"
    )
    for family in ("savedJob", "applicationNote", "profileSnippet", "draftMetadata"):
        _find(_function(records, "func refresh"), f"family: .{family}")
        _find(_function(records, "func payload"), f"case .{family}: return .{family}(")
    _find(_function(records, "func save"), "mutate(.init(updates:")
    _find(_function(records, "func save"), "mutate(.init(creates:")
    _find(_function(records, "func delete"), "mutate(.init(deletes:")
    _find(
        _function(records, "private func mutate"),
        "apply(.init(expectedVaultID: vaultID, mutations: mutations))",
    )
    searches = SWIFT + "AtlasVaultSavedSearchFeature.swift"
    for action, mutation in (
        ("create", "creates"),
        ("update", "updates"),
        ("delete", "deletes"),
    ):
        _find(
            _function(
                SWIFT + "AtlasVaultSavedSearchView.swift", f"public func {action}"
            ),
            f"coordinator.{action}(",
        )
        _ordered(
            _function(searches, f"public func {action}"),
            f"AtlasVaultMutationSet({mutation}: [mutation])",
            "performMutation(runtimeRequest)",
        )
    _ordered(
        _function(searches, "private func performMutation"),
        "let apply = environment.applyPrivateMutation",
        "await apply(request)",
    )


@pytest.fixture(scope="module")
def runtime_record_validator() -> Draft202012Validator:
    schema = json.loads(
        (ROOT / "contracts/sync/atlasvault_runtime_record_v1.schema.json").read_text(
            encoding="utf-8"
        )
    )
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema, format_checker=FormatChecker())


def _synthetic_runtime_record(family: str) -> dict:
    vectors = json.loads(
        (
            ROOT / "contracts/sync/test_vectors/atlasvault_payload_vectors_v1.json"
        ).read_text(encoding="utf-8")
    )
    assert set(vectors["payloads"]) == INTENDED_FAMILIES
    return {
        "format": "atlasvault-runtime-record",
        "version": 1,
        "operation_id": "10000000-0000-4000-8000-000000000001",
        "author_device_id": "synthetic-device",
        "author_sequence": 1,
        "lamport": 1,
        "object_id": "synthetic-record",
        "revision": "20000000-0000-4000-8000-000000000001",
        "parent_revision": None,
        "tombstone": False,
        "payload": deepcopy(vectors["payloads"][family]),
    }


def _assert_schema(validator, record, *, valid: bool) -> None:
    # Report only schema paths, never the synthetic or decrypted payload values.
    errors = ["/".join(map(str, e.schema_path)) for e in validator.iter_errors(record)]
    assert (not errors) == valid, f"Runtime record schema result: {errors}"


@pytest.mark.parametrize("family", sorted(INTENDED_FAMILIES))
def test_runtime_record_schema_accepts_synthetic_family_crud(
    runtime_record_validator, family
) -> None:
    record = _synthetic_runtime_record(family)
    _assert_schema(runtime_record_validator, record, valid=True)
    record["parent_revision"] = record["revision"]
    record["revision"] = "30000000-0000-4000-8000-000000000001"
    record["author_sequence"] = record["lamport"] = 2
    _assert_schema(runtime_record_validator, record, valid=True)
    record["tombstone"] = True
    record["payload"] = None
    _assert_schema(runtime_record_validator, record, valid=True)


@pytest.mark.parametrize("field", list(_synthetic_runtime_record("saved_search")))
def test_runtime_record_schema_requires_every_wrapper_field(
    runtime_record_validator, field
) -> None:
    record = _synthetic_runtime_record("saved_search")
    del record[field]
    _assert_schema(runtime_record_validator, record, valid=False)


@pytest.mark.parametrize(
    "field,value",
    [
        ("format", "other"),
        ("version", 2),
        ("version", True),
        ("operation_id", "invalid"),
        ("revision", "invalid"),
        ("author_device_id", ""),
        ("object_id", ""),
        ("parent_revision", 7),
        ("parent_revision", "invalid"),
        ("author_sequence", 0),
        ("author_sequence", True),
        ("author_sequence", 1.5),
        ("lamport", 0),
        ("lamport", True),
        ("lamport", 2**63),
        ("tombstone", "false"),
        ("payload", None),
        ("payload", []),
        ("unexpected", "synthetic"),
    ],
)
def test_runtime_record_schema_rejects_malformed_wrappers(
    runtime_record_validator, field, value
) -> None:
    record = _synthetic_runtime_record("saved_search")
    record[field] = value
    _assert_schema(runtime_record_validator, record, valid=False)


def test_runtime_record_schema_rejects_tombstone_with_payload(
    runtime_record_validator,
) -> None:
    record = _synthetic_runtime_record("application_note")
    record["tombstone"] = True
    _assert_schema(runtime_record_validator, record, valid=False)
