import 'dart:async';
import 'dart:io';

import 'package:atlas/atlas.dart';
import 'package:atlas/atlas_vault_android.dart';
import 'package:atlas/atlas_vault_windows.dart';
import 'package:flutter/cupertino.dart';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import 'atlas_cache_location.dart';

typedef AtlasClientFactory = AtlasAPIClient Function(Uri baseURL);
typedef AtlasCacheStoreFactory =
    Future<AtlasLocalCacheStore?> Function({
      bool Function()? privateStateProtectionActive,
    });

final class _AtlasConnectionOperation {
  const _AtlasConnectionOperation({
    required this.identifier,
    required this.sourceAuthority,
  });

  final int identifier;
  final Uri sourceAuthority;
}

final class _AtlasStaleConnectionOperation implements Exception {
  const _AtlasStaleConnectionOperation();

  @override
  String toString() => 'Atlas connection operation is no longer current.';
}

const MethodChannel _storageChannel = MethodChannel('atlas/storage');
const _plaintextAuthorityUnavailableMessage =
    'Private data is unavailable while AtlasVault migration is pending.';

Future<AtlasLocalCacheStore?> _defaultCacheStore({
  bool Function()? privateStateProtectionActive,
}) async {
  if (Platform.isAndroid) {
    try {
      // coverage:ignore-start
      final directoryPath = await _storageChannel.invokeMethod<String>(
        'appFilesDir',
      );
      if (directoryPath != null && directoryPath.trim().isNotEmpty) {
        return AtlasLocalCacheStore(
          file: File('${directoryPath.trim()}/atlas-local-cache-v1.json'),
          privateStateProtectionActive: privateStateProtectionActive,
        );
      }
      // coverage:ignore-end
    } catch (_) {
      // Android has no temporary-directory fallback if native storage fails.
    }
  }
  if (isAtlasLegacyTemporaryCachePlatform()) {
    return AtlasLocalCacheStore(
      file: resolveAtlasLegacyTemporaryCacheFile(),
      privateStateProtectionActive: privateStateProtectionActive,
    );
  }
  if (!isAtlasPersistentDesktopCachePlatform()) {
    return null;
  }
  try {
    final cacheLocation = await resolveAtlasPersistentCacheLocation(
      importLegacyCache: !(privateStateProtectionActive?.call() ?? false),
    );
    return AtlasLocalCacheStore(
      file: cacheLocation.cacheFile,
      privateStateProtectionActive: privateStateProtectionActive,
      retainedLegacyPrivateStateAdmission: () => AtlasLocalCacheStore(
        file: cacheLocation.legacyFile,
      ).containsPersistedPrivateState(),
      prepareForClear: cacheLocation.prepareForClearUnderMutationLock,
      mutationCoordinator: cacheLocation.coordinateMutation,
    );
  } catch (_) {
    // A persistent cache is optional. Never fall back to an OS-managed
    // temporary directory because it cannot provide reliable offline storage.
    return null;
  }
}

class AtlasAppController extends ChangeNotifier
    implements
        AtlasVaultPlaintextMigrationOperationAdmission,
        AtlasVaultRecoveryImportOperationAdmission,
        AtlasVaultLegacyPrivateStateRestoring {
  AtlasAppController({
    Uri? initialBaseURL,
    AtlasClientFactory? clientFactory,
    AtlasLocalCacheStore? localCacheStore,
    AtlasCacheStoreFactory? localCacheStoreFactory,
    AtlasVaultPrivateStatePersistence? privateStatePersistence,
    AtlasVaultPlaintextAuthorityAdmission? plaintextAuthorityAdmission,
    Future<bool> Function()? compatibilityPrivateStateAdmission,
    Future<bool> Function()? recoveryImportPending,
    DateTime Function()? now,
    Timer Function(Duration, void Function())? searchDebounceTimerFactory,
  }) : baseURL = initialBaseURL ?? Uri.parse('http://10.253.1.43:8765'),
       _clientFactory =
           clientFactory ?? ((baseURL) => AtlasAPIClient(baseURL: baseURL)),
       // Keep public constructor parameter names stable while storing privately.
       // ignore: prefer_initializing_formals
       _localCacheStore = localCacheStore,
       // ignore: prefer_initializing_formals
       _localCacheStoreFactory = localCacheStoreFactory,
       // Keep the compatibility constructor side-effect free.
       // ignore: prefer_initializing_formals
       _privateStatePersistence = privateStatePersistence,
       // ignore: prefer_initializing_formals
       _plaintextAuthorityAdmission = plaintextAuthorityAdmission,
       // ignore: prefer_initializing_formals
       _compatibilityPrivateStateAdmission = compatibilityPrivateStateAdmission,
       // ignore: prefer_initializing_formals
       _recoveryImportPending = recoveryImportPending,
       _now = now ?? DateTime.now,
       _searchDebounceTimerFactory =
           searchDebounceTimerFactory ??
           ((duration, callback) => Timer(duration, callback));

  Uri baseURL;
  final AtlasClientFactory _clientFactory;
  String connectionStatus = 'Not connected';
  String? connectionMessage;
  bool isTesting = false;
  bool isSaving = false;
  bool isSavingSearch = false;
  bool isRefreshingLocalSave = false;
  bool isSearching = false;
  String query = '';
  AtlasSearchFilters filters = AtlasSearchFilters();
  SortOrder sortOrder = SortOrder.closingSoon;
  List<JobSearchResult> results = const [];
  List<AtlasSavedSearch> savedSearches = const [];
  List<AtlasSourceRun> updateRuns = const [];
  List<AtlasSourceSummary> sources = const [];
  List<AtlasApplicationRecord> trackerRecords = const [];
  List<JobSearchResult> _cachedAllJobs = const [];
  Map<String, AtlasJobDetail> _cachedJobDetails =
      const <String, AtlasJobDetail>{};
  AtlasSearchRequest? _committedPublicSearchRequest;
  AtlasHealthSummary? healthSummary;
  Map<String, Map<String, int>> facets = const {};
  Map<String, Map<String, String>> facetLabels = const {};
  int unclassifiedCount = 0;
  int total = 0;
  int cachedJobCount = 0;
  DateTime? cacheSavedAt;
  DateTime? operationalDataLoadedAt;
  Timer? _searchDebounce;
  bool _searchRefreshPendingAfterPrivateTransition = false;
  int _savedSearchSequence = 0;
  AtlasLocalCacheStore? _localCacheStore;
  final AtlasCacheStoreFactory? _localCacheStoreFactory;
  final AtlasVaultPrivateStatePersistence? _privateStatePersistence;
  final AtlasVaultPlaintextAuthorityAdmission? _plaintextAuthorityAdmission;
  final Future<bool> Function()? _compatibilityPrivateStateAdmission;
  final Future<bool> Function()? _recoveryImportPending;
  final DateTime Function() _now;
  final Timer Function(Duration, void Function()) _searchDebounceTimerFactory;
  int _privateAuthorityGeneration = 0;
  Uri? _savedSearchLegacyAuthorityBaseURL;
  Uri? _trackerLegacyAuthorityBaseURL;
  bool _privateActivationInProgress = false;
  bool _privateDeactivationInProgress = false;
  Future<void>? _privateDeactivationOperation;
  Future<void>? _cacheMutationOperation;
  Future<void>? _compatibilityPrivateMutationOperation;
  bool _crossProcessPlaintextAuthorityBlocked = false;
  AtlasVaultPlaintextMigrationContext? _plaintextMigrationContext;
  AtlasVaultInteroperabilityContext? _interoperabilityContext;
  AtlasVaultTrustedPairingContext? _trustedPairingContext;
  bool _recoveryImportAdmissionInProgress = false;
  bool _recoveryImportBlocksLegacyPrivateAuthority = false;
  int _connectionOperationSequence = 0;
  _AtlasConnectionOperation? _activeConnectionOperation;
  int? _testingConnectionOperationIdentifier;
  int? _savingConnectionOperationIdentifier;
  int? _refreshingConnectionOperationIdentifier;
  int? _searchingConnectionOperationIdentifier;

  bool get _privateStateProtectionActive {
    return _privateActivationInProgress ||
        _privateDeactivationInProgress ||
        _recoveryImportAdmissionInProgress ||
        _recoveryImportBlocksLegacyPrivateAuthority ||
        (_plaintextMigrationContext?.owner.blocksLegacyPrivateAuthority ??
            false) ||
        (_trustedPairingContext?.owner.blocksLegacyPrivateAuthority ?? false) ||
        (_privateStatePersistence?.isActive ?? false);
  }

  bool get _plaintextMigrationBlocksPersistedCacheWrites {
    return _recoveryImportAdmissionInProgress ||
        (_plaintextMigrationContext?.owner.blocksPersistedCacheWrites ??
            false) ||
        (_trustedPairingContext?.owner.blocksPersistedCacheWrites ?? false);
  }

  AtlasVaultPlaintextMigrationContext? get plaintextMigrationContext =>
      _plaintextMigrationContext;

  AtlasVaultInteroperabilityContext? get interoperabilityContext =>
      _interoperabilityContext;

  AtlasVaultTrustedPairingContext? get trustedPairingContext =>
      _trustedPairingContext;

  void attachPlaintextMigrationContext(
    AtlasVaultPlaintextMigrationContext context,
  ) {
    if (_plaintextMigrationContext != null) {
      throw const AtlasVaultPlaintextMigrationException();
    }
    _plaintextMigrationContext = context;
  }

  void attachInteroperabilityContext(
    AtlasVaultInteroperabilityContext context,
  ) {
    if (_interoperabilityContext != null) {
      throw const AtlasVaultInteroperabilityException();
    }
    _interoperabilityContext = context;
  }

  void attachTrustedPairingContext(AtlasVaultTrustedPairingContext context) {
    if (_trustedPairingContext != null) {
      throw const AtlasVaultPairingTransactionException();
    }
    _trustedPairingContext = context;
  }

  void _recoveryImportPendingDidChange(bool pending) {
    _recoveryImportBlocksLegacyPrivateAuthority = pending;
    if (pending) {
      _hideLegacyPrivateStateForMigration();
    }
  }

  void clearConnectionMessage() {
    if (connectionMessage == null) {
      return;
    }
    connectionMessage = null;
    notifyListeners();
  }

  void reportValidationError(String message) {
    connectionStatus = 'Not connected';
    connectionMessage = message;
    notifyListeners();
  }

  String get statusSubtitle {
    if (isSearching || isRefreshingLocalSave) {
      return 'Refreshing from ${_formatBaseURL(baseURL)}';
    }
    if (cacheSavedAt != null) {
      return 'Local save · updated ${_formatSavedAt(cacheSavedAt!)}';
    }
    if (connectionStatus == 'Connected') {
      return 'Connected to ${_formatBaseURL(baseURL)}';
    }
    return 'Offline until API connection is configured';
  }

  String get resultCountLabel {
    final suffix = filters.openOnly
        ? total == 1
              ? 'searchable result'
              : 'searchable results'
        : total == 1
        ? 'result'
        : 'results';
    return '${_formatCount(total)} $suffix';
  }

  String get cacheFreshnessLabel {
    final savedAt = cacheSavedAt;
    if (savedAt == null) {
      return 'Empty';
    }
    final age = _now().difference(savedAt);
    return age > AtlasLocalCacheSnapshot.staleAfter ? 'Stale' : 'Fresh';
  }

  bool get canReconcileDefaultOpenCount {
    return query.trim().isEmpty &&
        filters.openOnly &&
        !filters.closingSoon &&
        filters.selectedCities.isEmpty &&
        filters.selectedCountriesISO3.isEmpty &&
        filters.scope == AtlasScopeFilter.any &&
        !filters.includeLowConfidence &&
        filters.gradeCodes.isEmpty &&
        filters.workModalities.isEmpty &&
        filters.sourceIDs.isEmpty &&
        filters.organizations.isEmpty &&
        filters.ccogFamilies.isEmpty &&
        filters.contractGroups.isEmpty &&
        filters.seniorityGroups.isEmpty &&
        filters.volunteerKinds.isEmpty &&
        filters.unvCategories.isEmpty &&
        filters.unvVolunteerTypes.isEmpty &&
        filters.capabilityTags.isEmpty &&
        filters.trimmedCapabilityQuery.isEmpty;
  }

  int get cachedDetailCount => _cachedJobDetails.length;

  int? get hiddenDeadlinePastOpenJobs {
    final openJobs = healthSummary?.openJobs;
    if (openJobs == null || !canReconcileDefaultOpenCount) {
      return null;
    }
    final hidden = openJobs - total;
    return hidden > 0 ? hidden : null;
  }

  String? get countReconciliationSummary {
    final hidden = hiddenDeadlinePastOpenJobs;
    if (hidden == null) {
      return null;
    }
    return '${_formatCount(hidden)} deadline-past open rows hidden by Search';
  }

  bool isJobSaved(String jobKey) {
    return trackerRecords.any(
      (record) => record.jobKey == jobKey && record.status != 'closed',
    );
  }

  @override
  void dispose() {
    _searchRefreshPendingAfterPrivateTransition = false;
    _activeConnectionOperation = null;
    _connectionOperationSequence += 1;
    _cancelSearchDebounce();
    super.dispose();
  }

  Future<AtlasVaultActivationResult> activateExistingAtlasVault(
    String vaultId,
  ) {
    return _activateExistingAtlasVault(
      vaultId,
      recoveryImportResume: false,
      plaintextMigrationResume: false,
    );
  }

  Future<AtlasVaultActivationResult> _activateImportedAtlasVault(
    String vaultId,
  ) {
    return _activateExistingAtlasVault(
      vaultId,
      recoveryImportResume: true,
      plaintextMigrationResume: false,
    );
  }

  Future<AtlasVaultActivationResult> _activateMigratedAtlasVault(
    String vaultId,
  ) {
    return _activateExistingAtlasVault(
      vaultId,
      recoveryImportResume: false,
      plaintextMigrationResume: true,
    );
  }

  Future<AtlasVaultActivationResult> _activateExistingAtlasVault(
    String vaultId, {
    required bool recoveryImportResume,
    required bool plaintextMigrationResume,
  }) async {
    if (_recoveryImportBlocksLegacyPrivateAuthority && !recoveryImportResume) {
      return AtlasVaultActivationResult.failed;
    }
    final persistence = _privateStatePersistence;
    if (persistence == null ||
        persistence.isActive ||
        _privateActivationInProgress ||
        _privateDeactivationInProgress) {
      return AtlasVaultActivationResult.failed;
    }
    _privateAuthorityGeneration += 1;
    final activationGeneration = _privateAuthorityGeneration;
    _privateActivationInProgress = true;
    _cancelSearchDebounce(rescheduleAfterTransition: true);
    try {
      final activationAuthority = _requiredNormalizedBaseURL(baseURL);
      if (!plaintextMigrationResume) {
        if (savedSearches.isNotEmpty || trackerRecords.isNotEmpty) {
          return AtlasVaultActivationResult.migrationRequired;
        }
        await _drainPlaintextOperationsForActivation(
          activationGeneration,
          activationAuthority,
        );
      }

      Future<AtlasVaultActivationResult> activateUnderAuthority() async {
        if (!plaintextMigrationResume) {
          final compatibilityPrivateStateAdmission =
              _compatibilityPrivateStateAdmission;
          if (compatibilityPrivateStateAdmission != null) {
            final containsCompatibilityPrivateState =
                await compatibilityPrivateStateAdmission();
            _requireCurrentPrivateActivation(
              activationGeneration,
              activationAuthority,
            );
            if (containsCompatibilityPrivateState) {
              return AtlasVaultActivationResult.migrationRequired;
            }
          }
          final cacheStore = await _ensureLocalCacheStore();
          _requireCurrentPrivateActivation(
            activationGeneration,
            activationAuthority,
          );
          final containsPersistedPrivateState =
              await cacheStore?.containsPersistedPrivateState() ?? false;
          _requireCurrentPrivateActivation(
            activationGeneration,
            activationAuthority,
          );
          if (containsPersistedPrivateState) {
            return AtlasVaultActivationResult.migrationRequired;
          }
        }
        final result = await persistence.activateExisting(vaultId);
        _requireCurrentPrivateActivation(
          activationGeneration,
          activationAuthority,
        );
        if (result != AtlasVaultActivationResult.activated) {
          return result;
        }
        final snapshot = await persistence.read();
        _requireCurrentPrivateActivation(
          activationGeneration,
          activationAuthority,
        );
        if (!persistence.isActive) {
          throw const AtlasVaultPrivateStateException();
        }
        _installPrivateSnapshot(snapshot);
        notifyListeners();
        return AtlasVaultActivationResult.activated;
      }

      if (plaintextMigrationResume ||
          recoveryImportResume ||
          _plaintextAuthorityAdmission == null) {
        return await activateUnderAuthority();
      }
      return await _runLegacyPrivateOperation(activateUnderAuthority);
    } catch (_) {
      try {
        await persistence.deactivate();
      } catch (_) {
        // The fixed failed result remains authoritative.
      }
      savedSearches = const <AtlasSavedSearch>[];
      trackerRecords = const <AtlasApplicationRecord>[];
      _clearLegacyPrivateProjectionAuthority();
      _syncSavedSearchSequence();
      notifyListeners();
      return AtlasVaultActivationResult.failed;
    } finally {
      _privateActivationInProgress = false;
      _resumeSearchDebounceAfterPrivateTransition();
    }
  }

  Future<void> deactivateAtlasVault() {
    final existing = _privateDeactivationOperation;
    if (existing != null) {
      return existing;
    }
    _privateAuthorityGeneration += 1;
    _privateDeactivationInProgress = true;
    _cancelSearchDebounce(rescheduleAfterTransition: true);
    late final Future<void> operation;
    operation = _performPrivateDeactivation().whenComplete(() {
      if (identical(_privateDeactivationOperation, operation)) {
        _privateDeactivationInProgress = false;
        _privateDeactivationOperation = null;
        _resumeSearchDebounceAfterPrivateTransition();
      }
    });
    _privateDeactivationOperation = operation;
    return operation;
  }

  Future<void> _performPrivateDeactivation() async {
    try {
      await _privateStatePersistence?.deactivate();
    } finally {
      savedSearches = const <AtlasSavedSearch>[];
      trackerRecords = const <AtlasApplicationRecord>[];
      _clearLegacyPrivateProjectionAuthority();
      _syncSavedSearchSequence();
      notifyListeners();
    }
  }

  Future<void> loadPersistedCache() async {
    Future<void> load() async {
      final store = await _ensureLocalCacheStore();
      if (store == null) {
        return;
      }
      final snapshot = await store.read();
      if (snapshot == null) {
        return;
      }
      _applyCacheSnapshot(snapshot);
      notifyListeners();
    }

    if (_plaintextAuthorityAdmission != null &&
        !_privateStateProtectionActive) {
      try {
        await _runLegacyPrivateOperation(load);
      } on AtlasVaultPlaintextAuthorityAdmissionException {
        return;
      }
    } else {
      await load();
    }
  }

  Future<void> bootstrapPrivateAuthorityAndLoadPersistedCache() async {
    final inspectRecoveryImport = _recoveryImportPending;
    if (inspectRecoveryImport != null) {
      try {
        _recoveryImportBlocksLegacyPrivateAuthority =
            await inspectRecoveryImport();
      } catch (_) {
        _recoveryImportBlocksLegacyPrivateAuthority = true;
      }
    }
    if (_recoveryImportBlocksLegacyPrivateAuthority) {
      _hideLegacyPrivateStateForMigration();
      await loadPersistedCache();
      return;
    }
    final context = _plaintextMigrationContext;
    if (context == null) {
      await loadPersistedCache();
      return;
    }
    await context.owner.bootstrapAuthority();
    if (context.owner.blocksLegacyPrivateAuthority) {
      _hideLegacyPrivateStateForMigration();
    }
    await loadPersistedCache();
  }

  @override
  Future<void> restoreLegacyPrivateStateAfterRollback(
    AtlasVaultPlaintextPrivateState reviewedState,
  ) async {
    final restorationGeneration = ++_privateAuthorityGeneration;
    final restorationAuthority = _requiredNormalizedBaseURL(baseURL);
    if (_requiredNormalizedBaseURL(reviewedState.authorityBaseURL) !=
        restorationAuthority) {
      throw const AtlasVaultPlaintextMigrationException();
    }
    _requireCurrentLegacyRollbackRestoration(
      restorationGeneration,
      restorationAuthority,
    );
    _installLegacyPrivateProjection(
      savedSearches: reviewedState.savedSearches,
      trackerRecords: reviewedState.trackerRecords,
      authorityBaseURL: restorationAuthority,
    );
    _crossProcessPlaintextAuthorityBlocked = false;
    notifyListeners();
  }

  Future<void> clearPersistedCache() async {
    if (_plaintextMigrationBlocksPersistedCacheWrites) {
      _publishPersistedCacheMigrationBlock();
      return;
    }
    final clearsLegacyPrivateState = !_privateStateProtectionActive;
    Future<void> clear() async {
      if (_plaintextMigrationBlocksPersistedCacheWrites) {
        _publishPersistedCacheMigrationBlock();
        return;
      }
      final store = await _ensureLocalCacheStore();
      if (_plaintextMigrationBlocksPersistedCacheWrites) {
        _publishPersistedCacheMigrationBlock();
        return;
      }
      await store?.clear();
      results = const [];
      updateRuns = const [];
      sources = const [];
      if (clearsLegacyPrivateState) {
        savedSearches = const [];
        trackerRecords = const [];
        _clearLegacyPrivateProjectionAuthority();
      }
      _cachedAllJobs = const [];
      _cachedJobDetails = const <String, AtlasJobDetail>{};
      _committedPublicSearchRequest = null;
      healthSummary = null;
      facets = const {};
      facetLabels = const {};
      unclassifiedCount = 0;
      total = 0;
      cachedJobCount = 0;
      cacheSavedAt = null;
      operationalDataLoadedAt = null;
      connectionStatus = 'Not connected';
      connectionMessage = 'Local cache cleared.';
      notifyListeners();
    }

    await _retainCacheMutation(() async {
      if (!clearsLegacyPrivateState) {
        await clear();
        return;
      }
      try {
        await _runLegacyPrivateOperation(clear);
      } on AtlasVaultPlaintextAuthorityAdmissionException {
        return;
      }
    });
  }

  void _publishPersistedCacheMigrationBlock() {
    connectionMessage =
        'Local cache changes are unavailable during AtlasVault migration.';
    notifyListeners();
  }

  void updateQuery(String value) {
    if (query == value) {
      return;
    }
    query = value;
    notifyListeners();
    _scheduleSearchIfReady();
  }

  bool isQuickFilterActive(String title) {
    return switch (title) {
      'Closing soon' => filters.closingSoon,
      'Remote' => filters.isRemoteOnly,
      'Best fit' => sortOrder == SortOrder.bestFit,
      _ => false,
    };
  }

  Future<void> testConnection(Uri candidateBaseURL) async {
    final operation = _beginConnectionOperation(candidateBaseURL);
    _testingConnectionOperationIdentifier = operation.identifier;
    isTesting = true;
    connectionMessage = null;
    notifyListeners();
    try {
      final client = _clientFactory(operation.sourceAuthority);
      final health = await client.health();
      _requireCurrentConnectionOperation(operation, client);
      healthSummary = health;
      connectionStatus = 'Connected';
      connectionMessage = _healthMessage(health);
      await _loadSavedSearches(client, operation);
      _requireCurrentConnectionOperation(operation, client);
      await _loadOperationalData(client, operation);
      _requireCurrentConnectionOperation(operation, client);
    } on _AtlasStaleConnectionOperation {
      // A newer connection operation owns all publishable state.
    } catch (error) {
      if (!_isCurrentConnectionOperation(operation)) {
        return;
      }
      connectionStatus = 'Not connected';
      connectionMessage = 'Connection failed: $error';
    } finally {
      if (_testingConnectionOperationIdentifier == operation.identifier) {
        _testingConnectionOperationIdentifier = null;
        isTesting = false;
        _completeConnectionOperation(operation);
        notifyListeners();
      }
    }
  }

  Future<void> saveAndReload(Uri candidateBaseURL) async {
    final operation = _beginConnectionOperation(candidateBaseURL);
    _savingConnectionOperationIdentifier = operation.identifier;
    isSaving = true;
    connectionMessage = null;
    notifyListeners();
    try {
      final client = _clientFactory(operation.sourceAuthority);
      final health = await client.health();
      _requireCurrentConnectionOperation(operation, client);
      healthSummary = health;
      await _preparePrivateAuthorityChange(operation, client);
      _requireCurrentConnectionOperation(operation, client);
      baseURL = operation.sourceAuthority;
      connectionStatus = 'Connected';
      final refreshed = await _refreshSearch(client, operation);
      _requireCurrentConnectionOperation(operation, client);
      await _loadSavedSearches(client, operation);
      _requireCurrentConnectionOperation(operation, client);
      await _loadOperationalData(client, operation);
      _requireCurrentConnectionOperation(operation, client);
      await _writePersistedCache();
      _requireCurrentConnectionOperation(operation, client);
      connectionMessage = _crossProcessPlaintextAuthorityBlocked
          ? _plaintextAuthorityUnavailableMessage
          : 'Saved ${_formatBaseURL(operation.sourceAuthority)} and refreshed $refreshed ${_jobWord(refreshed)}.';
    } on _AtlasStaleConnectionOperation {
      // A newer connection operation owns all publishable state.
    } catch (error) {
      if (!_isCurrentConnectionOperation(operation)) {
        return;
      }
      connectionStatus = 'Not connected';
      connectionMessage = 'Save failed: $error';
    } finally {
      if (_savingConnectionOperationIdentifier == operation.identifier) {
        _savingConnectionOperationIdentifier = null;
        isSaving = false;
        _completeConnectionOperation(operation);
        notifyListeners();
      }
    }
  }

  Future<void> refreshLocalSave() async {
    final operation = _beginConnectionOperation(baseURL);
    _refreshingConnectionOperationIdentifier = operation.identifier;
    isRefreshingLocalSave = true;
    connectionMessage = null;
    notifyListeners();
    try {
      final client = _clientFactory(operation.sourceAuthority);
      await _refreshHealthIfAvailable(client, operation);
      _requireCurrentConnectionOperation(operation, client);
      final refreshed = await _refreshSearch(client, operation);
      _requireCurrentConnectionOperation(operation, client);
      await _loadSavedSearches(client, operation);
      _requireCurrentConnectionOperation(operation, client);
      await _loadOperationalData(client, operation);
      _requireCurrentConnectionOperation(operation, client);
      await _writePersistedCache();
      _requireCurrentConnectionOperation(operation, client);
      connectionStatus = 'Connected';
      connectionMessage = _crossProcessPlaintextAuthorityBlocked
          ? _plaintextAuthorityUnavailableMessage
          : 'Local save refreshed: $refreshed ${_jobWord(refreshed)} cached on this device.';
    } on _AtlasStaleConnectionOperation {
      // A newer connection operation owns all publishable state.
    } catch (error) {
      if (!_isCurrentConnectionOperation(operation)) {
        return;
      }
      if (_cachedAllJobs.isNotEmpty) {
        _applyLocalSearch();
      }
      connectionStatus = cacheSavedAt == null
          ? 'Not connected'
          : 'Offline (cached)';
      connectionMessage = 'Local save refresh failed: $error';
    } finally {
      if (_refreshingConnectionOperationIdentifier == operation.identifier) {
        _refreshingConnectionOperationIdentifier = null;
        isRefreshingLocalSave = false;
        _completeConnectionOperation(operation);
        notifyListeners();
      }
    }
  }

  Future<void> setSortOrder(SortOrder order) async {
    if (sortOrder == order) {
      return;
    }
    sortOrder = order;
    notifyListeners();
    if (cacheSavedAt != null || connectionStatus == 'Connected') {
      await refreshLocalSave();
    }
  }

  Future<void> toggleQuickFilter(String title) async {
    switch (title) {
      case 'Closing soon':
        filters = filters.copyWith(closingSoon: !filters.closingSoon);
      case 'Remote':
        filters = filters.copyWith(
          workModalities: filters.isRemoteOnly
              ? <String>{}
              : AtlasSearchFilters.remoteWorkModalities,
        );
      case 'Best fit':
        sortOrder = sortOrder == SortOrder.bestFit
            ? SortOrder.closingSoon
            : SortOrder.bestFit;
      default:
        return;
    }
    notifyListeners();
    await _refreshIfReady();
  }

  Future<void> removeActiveFilter(String id) async {
    filters = filters.removingChip(id);
    notifyListeners();
    await _refreshIfReady();
  }

  Future<void> setOpenOnly(bool value) async {
    filters = filters.copyWith(openOnly: value);
    notifyListeners();
    await _refreshIfReady();
  }

  Future<void> applyFilters(AtlasSearchFilters nextFilters) async {
    filters = nextFilters;
    notifyListeners();
    await _refreshIfReady();
  }

  Future<void> resetFilters() async {
    filters = AtlasSearchFilters();
    sortOrder = SortOrder.closingSoon;
    notifyListeners();
    await _refreshIfReady();
  }

  Future<void> saveCurrentSearch() async {
    isSavingSearch = true;
    connectionMessage = null;
    notifyListeners();
    final request = _currentSearchRequest();
    final name = _nextSavedSearchName();
    final summary = _savedSearchSummary(request);
    final privateAuthorityGeneration = _privateAuthorityGeneration;
    try {
      final persistence = _privateStatePersistence;
      if (persistence?.isActive ?? false) {
        final snapshot = await persistence!.saveSearch(
          AtlasSavedSearch(name: name, description: summary, request: request),
        );
        if (!persistence.isActive) {
          throw const AtlasVaultPrivateStateException();
        }
        _installPrivateSnapshot(snapshot);
      } else {
        if (_privateStateProtectionActive ||
            _privateActivationInProgress ||
            _privateDeactivationInProgress) {
          throw const AtlasVaultPrivateStateException();
        }
        final client = _clientFactory(baseURL);
        await _retainCompatibilityPrivateMutation(
          () => _runLegacyPrivateOperation(() async {
            _requireLegacySavedSearchMutationAuthority();
            final savedSearch = await client.saveSearch(
              name: name,
              request: request,
              summary: summary,
            );
            if (!_mayAcceptCompatibilityMutation(privateAuthorityGeneration)) {
              throw const AtlasVaultPrivateStateException();
            }
            _upsertSavedSearch(savedSearch);
            _savedSearchLegacyAuthorityBaseURL = _requiredNormalizedBaseURL(
              baseURL,
            );
          }),
        );
      }
      await _writePersistedCache();
      connectionStatus = 'Connected';
      connectionMessage = 'Saved $name locally.';
    } catch (error) {
      connectionMessage =
          error is AtlasVaultPlaintextAuthorityAdmissionException
          ? _plaintextAuthorityUnavailableMessage
          : 'Save search failed: $error';
    } finally {
      isSavingSearch = false;
      notifyListeners();
    }
  }

  Future<void> saveJob(JobSearchResult job) async {
    connectionMessage = null;
    notifyListeners();
    final privateAuthorityGeneration = _privateAuthorityGeneration;
    try {
      final persistence = _privateStatePersistence;
      if (persistence?.isActive ?? false) {
        final snapshot = await persistence!.saveTrackerRecord(
          AtlasApplicationRecord(id: '', jobKey: job.jobKey, status: 'saved'),
        );
        if (!persistence.isActive) {
          throw const AtlasVaultPrivateStateException();
        }
        _installPrivateSnapshot(snapshot);
      } else {
        if (_privateStateProtectionActive ||
            _privateActivationInProgress ||
            _privateDeactivationInProgress) {
          throw const AtlasVaultPrivateStateException();
        }
        final client = _clientFactory(baseURL);
        await _retainCompatibilityPrivateMutation(
          () => _runLegacyPrivateOperation(() async {
            _requireLegacyTrackerMutationAuthority();
            final record = await client.saveJob(job.jobKey);
            if (!_mayAcceptCompatibilityMutation(privateAuthorityGeneration)) {
              throw const AtlasVaultPrivateStateException();
            }
            _upsertTrackerRecord(record);
            _trackerLegacyAuthorityBaseURL = _requiredNormalizedBaseURL(
              baseURL,
            );
          }),
        );
      }
      await _writePersistedCache();
      connectionStatus = 'Connected';
      connectionMessage = 'Saved job locally.';
    } catch (error) {
      connectionMessage =
          error is AtlasVaultPlaintextAuthorityAdmissionException
          ? _plaintextAuthorityUnavailableMessage
          : 'Save job failed: $error';
    } finally {
      notifyListeners();
    }
  }

  Future<AtlasJobDetail> loadJobDetail(String jobKey) async {
    final cached = _cachedJobDetails[jobKey];
    if (cached != null) {
      return cached;
    }
    final client = _clientFactory(baseURL);
    try {
      final detail = await client.jobDetail(jobKey);
      _cachedJobDetails = Map.unmodifiable({
        ..._cachedJobDetails,
        jobKey: detail,
      });
      await _writePersistedCache();
      notifyListeners();
      return detail;
    } catch (_) {
      final fallback = _cachedJobDetails[jobKey];
      if (fallback != null) {
        return fallback;
      }
      rethrow;
    }
  }

  Future<void> setSourceFilter(String sourceID) async {
    filters = filters.copyWith(sourceIDs: <String>{sourceID});
    notifyListeners();
    await _refreshIfReady();
  }

  Future<void> runSavedSearch(AtlasSavedSearch search) async {
    query = search.request.text ?? '';
    filters = _filtersFromRequest(search.request);
    sortOrder = SortOrder.fromAPIValue(search.request.sort);
    notifyListeners();
    await _refreshIfReady();
  }

  Future<int> _refreshSearch(
    AtlasAPIClient client,
    _AtlasConnectionOperation operation,
  ) async {
    final publicAuthorityGeneration = _privateAuthorityGeneration;
    final mayPublishPublicRequest = !_privateStateProtectionActive;
    _requireCurrentConnectionOperation(operation, client);
    _searchingConnectionOperationIdentifier = operation.identifier;
    isSearching = true;
    notifyListeners();
    try {
      final activeRequest = _currentSearchRequest();
      final cacheRequest = _cacheSearchRequest();
      AtlasSearchResponse? cacheResponse;
      if (!_searchRequestsEquivalent(activeRequest, cacheRequest)) {
        cacheResponse = await _fetchCachedAllJobs(client, cacheRequest);
        _requireCurrentConnectionOperation(operation, client);
      }
      final response = await client.search(activeRequest);
      _requireCurrentConnectionOperation(operation, client);
      _applySearchResponse(response);
      if (_searchRequestsEquivalent(activeRequest, cacheRequest)) {
        _cachedAllJobs = List.unmodifiable(response.results);
      } else if (cacheResponse != null) {
        _cachedAllJobs = List.unmodifiable(cacheResponse.results);
        facetLabels = _mergeFacetLabels(facetLabels, cacheResponse.facetLabels);
      } else if (_cachedAllJobs.isEmpty) {
        _cachedAllJobs = List.unmodifiable(response.results);
      }
      cachedJobCount = _cachedAllJobs.isEmpty
          ? response.results.length
          : _cachedAllJobs.length;
      cacheSavedAt = _now();
      if (mayPublishPublicRequest &&
          !_privateStateProtectionActive &&
          _privateAuthorityGeneration == publicAuthorityGeneration) {
        _committedPublicSearchRequest = activeRequest;
      }
      return cachedJobCount;
    } finally {
      if (_searchingConnectionOperationIdentifier == operation.identifier) {
        _searchingConnectionOperationIdentifier = null;
        isSearching = false;
      }
    }
  }

  Future<AtlasSearchResponse?> _fetchCachedAllJobs(
    AtlasAPIClient client,
    AtlasSearchRequest cacheRequest,
  ) async {
    try {
      return await client.search(cacheRequest);
    } catch (_) {
      return null;
    }
  }

  List<AtlasFacetOption> facetOptions(String key, {int limit = 8}) {
    return _facetOptions(
      facets[key] ?? const <String, int>{},
      facetLabels[key] ?? const <String, String>{},
      limit: limit,
    );
  }

  List<AtlasFacetOption> availabilityFacetOptions(
    String key, {
    AtlasSearchFilters? filters,
    int limit = 8,
    Set<String> selected = const <String>{},
  }) {
    final activeFilters = filters ?? this.filters;
    final availability = _filterAvailabilityFacets(activeFilters);
    final values = availability[key] ?? facets[key] ?? const <String, int>{};
    final labels = _localFacetLabels()[key] ?? facetLabels[key] ?? const {};
    final options = _facetOptions(values, labels, limit: limit).toList();
    final existingIDs = options.map((option) => option.id).toSet();
    final sortedSelected = selected.toList()..sort();
    for (final value in sortedSelected) {
      if (existingIDs.contains(value)) {
        continue;
      }
      options.add(
        AtlasFacetOption(
          id: value,
          title: labels[value] ?? displayAtlasFilterValue(value),
          count: values[value] ?? 0,
        ),
      );
    }
    return options;
  }

  bool isFilterOptionEnabled({
    required String key,
    required String value,
    AtlasSearchFilters? filters,
  }) {
    final availability = _filterAvailabilityFacets(filters ?? this.filters);
    final values = availability[key];
    if (values == null) {
      return true;
    }
    return (values[value] ?? 0) > 0;
  }

  Future<void> _loadSavedSearches(
    AtlasAPIClient client,
    _AtlasConnectionOperation operation,
  ) async {
    _requireCurrentConnectionOperation(operation, client);
    final persistence = _privateStatePersistence;
    final authorityGeneration = _privateAuthorityGeneration;
    if (persistence?.isActive ?? false) {
      try {
        final snapshot = await persistence!.read();
        _requireCurrentConnectionOperation(operation, client);
        if (!_mayAcceptPrivateRead(persistence, authorityGeneration)) {
          return;
        }
        _installPrivateSnapshot(snapshot);
      } on _AtlasStaleConnectionOperation {
        rethrow;
      } catch (_) {
        // The last committed encrypted projection remains authoritative.
      }
      return;
    }
    if (_privateStateProtectionActive ||
        _privateActivationInProgress ||
        _privateDeactivationInProgress) {
      return;
    }
    try {
      await _runLegacyPrivateOperation(() async {
        final compatibilitySearches = await client.savedSearches();
        _requireCurrentCompatibilityConnectionOperation(operation, client);
        if (!_mayAcceptCompatibilityMutation(authorityGeneration)) {
          return;
        }
        savedSearches = List.unmodifiable(compatibilitySearches);
        _savedSearchLegacyAuthorityBaseURL = _requiredNormalizedBaseURL(
          client.baseURL,
        );
        _syncSavedSearchSequence();
      });
    } on _AtlasStaleConnectionOperation {
      rethrow;
    } catch (_) {
      _requireCurrentConnectionOperation(operation, client);
      // Saved-search persistence is not required for health/search success.
    }
  }

  Future<void> _loadOperationalData(
    AtlasAPIClient client,
    _AtlasConnectionOperation operation,
  ) async {
    _requireCurrentConnectionOperation(operation, client);
    try {
      final loadedRuns = await client.updates();
      _requireCurrentConnectionOperation(operation, client);
      updateRuns = List.unmodifiable(loadedRuns);
    } on _AtlasStaleConnectionOperation {
      rethrow;
    } catch (_) {
      _requireCurrentConnectionOperation(operation, client);
      // Operational summaries are best-effort and should not block Search.
    }
    try {
      final loadedSources = await client.sources();
      _requireCurrentConnectionOperation(operation, client);
      sources = List.unmodifiable(loadedSources);
    } on _AtlasStaleConnectionOperation {
      rethrow;
    } catch (_) {
      _requireCurrentConnectionOperation(operation, client);
      // Source-health summaries are best-effort and should not block Search.
    }
    final authorityGeneration = _privateAuthorityGeneration;
    if (!_privateStateProtectionActive && !_privateActivationInProgress) {
      try {
        await _runLegacyPrivateOperation(() async {
          final compatibilityRecords = await client.trackerRecords();
          _requireCurrentCompatibilityConnectionOperation(operation, client);
          if (_mayAcceptCompatibilityMutation(authorityGeneration)) {
            trackerRecords = List.unmodifiable(compatibilityRecords);
            _trackerLegacyAuthorityBaseURL = _requiredNormalizedBaseURL(
              client.baseURL,
            );
          }
        });
      } on _AtlasStaleConnectionOperation {
        rethrow;
      } catch (_) {
        _requireCurrentConnectionOperation(operation, client);
        // Saved-job persistence is independent from Search refresh.
      }
    }
    _requireCurrentConnectionOperation(operation, client);
    operationalDataLoadedAt = _now();
  }

  Future<void> _refreshHealthIfAvailable(
    AtlasAPIClient client,
    _AtlasConnectionOperation operation,
  ) async {
    try {
      final health = await client.health();
      _requireCurrentConnectionOperation(operation, client);
      healthSummary = health;
    } on _AtlasStaleConnectionOperation {
      rethrow;
    } catch (_) {
      _requireCurrentConnectionOperation(operation, client);
      // Search can still succeed when the health probe is temporarily stale.
    }
  }

  AtlasSearchRequest _currentSearchRequest({int limit = 10000}) {
    return AtlasSearchRequest.fromFilters(
      filters: filters,
      query: query,
      sortOrder: sortOrder,
      limit: limit,
    );
  }

  AtlasSearchRequest _cacheSearchRequest({int limit = 10000}) {
    return AtlasSearchRequest.fromFilters(
      filters: AtlasSearchFilters(),
      query: '',
      sortOrder: SortOrder.closingSoon,
      limit: limit,
    );
  }

  Future<void> _refreshIfReady() async {
    if (cacheSavedAt != null || connectionStatus == 'Connected') {
      await refreshLocalSave();
    }
  }

  void _scheduleSearchIfReady() {
    _searchDebounce?.cancel();
    if (_privateActivationInProgress ||
        _privateDeactivationInProgress ||
        (cacheSavedAt == null && connectionStatus != 'Connected')) {
      if (_privateActivationInProgress || _privateDeactivationInProgress) {
        _searchRefreshPendingAfterPrivateTransition = true;
      }
      return;
    }
    _searchDebounce = _searchDebounceTimerFactory(
      const Duration(milliseconds: 350),
      () {
        if (_privateActivationInProgress || _privateDeactivationInProgress) {
          _searchRefreshPendingAfterPrivateTransition = true;
          return;
        }
        refreshLocalSave();
      },
    );
  }

  void _cancelSearchDebounce({bool rescheduleAfterTransition = false}) {
    if (rescheduleAfterTransition && (_searchDebounce?.isActive ?? false)) {
      _searchRefreshPendingAfterPrivateTransition = true;
    }
    _searchDebounce?.cancel();
    _searchDebounce = null;
  }

  void _resumeSearchDebounceAfterPrivateTransition() {
    if (!_searchRefreshPendingAfterPrivateTransition ||
        _privateActivationInProgress ||
        _privateDeactivationInProgress) {
      return;
    }
    _searchRefreshPendingAfterPrivateTransition = false;
    _scheduleSearchIfReady();
  }

  String _savedSearchSummary(AtlasSearchRequest request) {
    final parts = <String>[];
    if (request.text != null && request.text!.trim().isNotEmpty) {
      parts.add('Query: ${request.text!.trim()}');
    }
    parts.add('${filters.activeChips.length} filters');
    parts.add('Sort: ${sortOrder.label}');
    return parts.join(' · ');
  }

  String _nextSavedSearchName() {
    _syncSavedSearchSequence();
    _savedSearchSequence += 1;
    return 'Search $_savedSearchSequence';
  }

  void _syncSavedSearchSequence() {
    final searchNamePattern = RegExp(r'^Search (\d+)$');
    for (final savedSearch in savedSearches) {
      final match = searchNamePattern.firstMatch(savedSearch.name.trim());
      if (match == null) {
        continue;
      }
      final index = int.tryParse(match.group(1) ?? '');
      if (index != null && index > _savedSearchSequence) {
        _savedSearchSequence = index;
      }
    }
  }

  void _applySearchResponse(AtlasSearchResponse response) {
    results = List.unmodifiable(response.results);
    total = response.total;
    facets = Map.unmodifiable(response.facets);
    facetLabels = Map.unmodifiable(response.facetLabels);
    unclassifiedCount = response.unclassifiedCount;
  }

  void _applyLocalSearch() {
    final rows = _sortedLocalRows(
      _filteredLocalRows(filters: filters, query: query, rows: _cachedAllJobs),
    );
    results = List.unmodifiable(rows);
    total = rows.length;
    facets = Map.unmodifiable(_localFacets(rows));
    facetLabels = Map.unmodifiable(_localFacetLabels());
    unclassifiedCount = rows
        .where((job) => _isUnknownGrade(job.gradeCode))
        .length;
  }

  List<JobSearchResult> _filteredLocalRows({
    required AtlasSearchFilters filters,
    required String query,
    required List<JobSearchResult> rows,
  }) {
    final textTerms = query
        .trim()
        .toLowerCase()
        .split(RegExp(r'\s+'))
        .where((value) => value.isNotEmpty)
        .toList();
    final cities = filters.selectedCities
        .map((value) => value.toLowerCase())
        .toSet();
    final countries = filters.selectedCountriesISO3;
    final scopeValues = filters.scope.apiValues.map(_normalizedToken).toSet();
    final gradeValues = filters.sortedGradeCodes.map(_normalizedGrade).toSet();
    final workModes = filters.workModalities.map(_normalizedToken).toSet();
    final contractGroups = filters.contractGroups.map(_normalizedToken).toSet();
    final seniorityGroups = filters.seniorityGroups
        .map(_normalizedToken)
        .toSet();
    final volunteerKinds = filters.volunteerKinds.map(_normalizedToken).toSet();
    final capabilityTerms = filters.capabilityTerms
        .map((value) => value.toLowerCase())
        .toList();

    return rows
        .where((job) {
          if (filters.openOnly) {
            if (job.status.toLowerCase() != 'open') {
              return false;
            }
            if (_isDeadlinePast(job, now: _now())) {
              return false;
            }
          }
          if (filters.closingSoon && !_isClosingSoon(job)) {
            return false;
          }
          if (cities.isNotEmpty &&
              !cities.any((city) => _jobMatchesCity(job, city))) {
            return false;
          }
          if (countries.isNotEmpty &&
              !countries.any((country) => _jobMatchesCountry(job, country))) {
            return false;
          }
          if (filters.sourceIDs.isNotEmpty &&
              !filters.sourceIDs.contains(job.sourceID)) {
            return false;
          }
          if (filters.organizations.isNotEmpty &&
              !filters.organizations.contains(job.organization) &&
              !filters.organizations.contains(job.organizationDisplay)) {
            return false;
          }
          if (scopeValues.isNotEmpty &&
              !scopeValues.contains(_localScopeValue(job))) {
            return false;
          }
          if (gradeValues.isNotEmpty &&
              !gradeValues.contains(_normalizedGrade(job.gradeCode))) {
            return false;
          }
          if (filters.ccogFamilies.isNotEmpty &&
              !filters.ccogFamilies.contains(job.ccogFamilyCode ?? '')) {
            return false;
          }
          if (workModes.isNotEmpty &&
              !workModes.contains(_normalizedToken(job.workModality))) {
            return false;
          }
          if (contractGroups.isNotEmpty &&
              !_localContractTokens(job).any(contractGroups.contains)) {
            return false;
          }
          if (seniorityGroups.isNotEmpty &&
              !seniorityGroups.contains(_localSeniorityValue(job))) {
            return false;
          }
          if (filters.volunteerKinds.isNotEmpty &&
              !_localVolunteerTokens(job).any(volunteerKinds.contains)) {
            return false;
          }
          if (capabilityTerms.isNotEmpty &&
              !capabilityTerms.any(
                (term) => _localSearchText(job).contains(term),
              )) {
            return false;
          }
          if (textTerms.isNotEmpty &&
              !textTerms.every(
                (term) => _localSearchText(job).contains(term),
              )) {
            return false;
          }
          return true;
        })
        .toList(growable: false);
  }

  Map<String, Map<String, int>> _filterAvailabilityFacets(
    AtlasSearchFilters filters,
  ) {
    final sourceRows = _cachedAllJobs.isNotEmpty ? _cachedAllJobs : results;
    if (sourceRows.isEmpty) {
      return const <String, Map<String, int>>{};
    }
    final output = <String, Map<String, int>>{};
    for (final scope in _FilterAvailabilityScope.values) {
      final scopedRows = _filteredLocalRows(
        filters: _clearedFilters(filters, scope),
        query: query,
        rows: sourceRows,
      );
      final scopedFacets = _localFacets(scopedRows);
      for (final key in scope.facetKeys) {
        output[key] = scopedFacets[key] ?? const <String, int>{};
      }
    }
    return output;
  }

  Map<String, Map<String, int>> _localFacets(List<JobSearchResult> rows) {
    final output = <String, Map<String, int>>{};
    for (final job in rows) {
      _incrementFacet(output, key: 'organizations', value: job.organization);
      _incrementFacet(output, key: 'source_ids', value: job.sourceID);
      _incrementFacet(output, key: 'cities', value: job.city);
      _incrementFacet(output, key: 'countries', value: job.countryISO3);
      _incrementFacet(
        output,
        key: 'grades',
        value: _normalizedGrade(job.gradeCode),
        skip: _isUnknownGrade(job.gradeCode),
      );
      _incrementFacet(output, key: 'ccog_families', value: job.ccogFamilyCode);
      _incrementFacet(output, key: 'contract_groups', value: job.contractGroup);
      _incrementFacet(
        output,
        key: 'seniority_groups',
        value: _localSeniorityValue(job),
      );
      _incrementFacet(
        output,
        key: 'work_modalities',
        value: _normalizedToken(job.workModality),
      );
      for (final tag in job.capabilityTags) {
        _incrementFacet(output, key: 'capability_tags', value: tag);
      }
      for (final value in _localVolunteerTokens(job)) {
        _incrementFacet(output, key: 'volunteer_kinds', value: value);
      }
    }
    return output;
  }

  Map<String, Map<String, String>> _localFacetLabels() {
    final labels = _mergeFacetLabels(facetLabels, const {});
    final organizationLabels = Map<String, String>.of(
      labels['organizations'] ?? const <String, String>{},
    );
    for (final job in _cachedAllJobs) {
      organizationLabels[job.organization] =
          organizationLabels[job.organization] ?? job.organizationDisplay;
    }
    labels['organizations'] = organizationLabels;

    final sourceLabels = Map<String, String>.of(
      labels['source_ids'] ?? const <String, String>{},
    );
    for (final source in sources) {
      sourceLabels[source.sourceID] =
          sourceLabels[source.sourceID] ?? source.organization;
    }
    labels['source_ids'] = sourceLabels;

    final gradeLabels = Map<String, String>.of(
      labels['grades'] ?? const <String, String>{},
    );
    for (final job in _cachedAllJobs) {
      final grade = _normalizedGrade(job.gradeCode);
      if (grade.isNotEmpty) {
        gradeLabels[grade] = gradeLabels[grade] ?? job.gradeCode;
      }
    }
    labels['grades'] = gradeLabels;

    labels['volunteer_kinds'] = {
      ...(labels['volunteer_kinds'] ?? const <String, String>{}),
      AtlasVolunteerKind.unVolunteer.value:
          AtlasVolunteerKind.unVolunteer.title,
      AtlasVolunteerKind.volunteer.value: AtlasVolunteerKind.volunteer.title,
    };
    labels['seniority_groups'] = {
      ...(labels['seniority_groups'] ?? const <String, String>{}),
      ...atlasSeniorityLabels,
    };
    return labels;
  }

  List<JobSearchResult> _sortedLocalRows(List<JobSearchResult> rows) {
    final sorted = rows.toList();
    sorted.sort((left, right) {
      return switch (sortOrder) {
        SortOrder.bestFit => ((right.score ?? -1).compareTo(left.score ?? -1)),
        SortOrder.deadlineLatest => _compareNullableDates(
          right.closingDate,
          left.closingDate,
          left.title,
          right.title,
        ),
        SortOrder.newestPosted => _compareNullableDates(
          right.postedDate,
          left.postedDate,
          left.title,
          right.title,
        ),
        SortOrder.closingSoon => _compareNullableDates(
          left.closingDate,
          right.closingDate,
          left.title,
          right.title,
        ),
      };
    });
    return sorted;
  }

  void _applyCacheSnapshot(AtlasLocalCacheSnapshot snapshot) {
    baseURL = snapshot.baseURL;
    _committedPublicSearchRequest = snapshot.searchRequest;
    query = snapshot.searchRequest.text ?? '';
    filters = _filtersFromRequest(snapshot.searchRequest);
    sortOrder = SortOrder.fromAPIValue(snapshot.searchRequest.sort);
    _cachedAllJobs = List.unmodifiable(snapshot.cachedAllJobs);
    if (_cachedAllJobs.isEmpty) {
      _applySearchResponse(snapshot.searchResponse);
      _cachedAllJobs = List.unmodifiable(snapshot.searchResponse.results);
    } else {
      _applyLocalSearch();
    }
    if (!_privateStateProtectionActive) {
      savedSearches = List.unmodifiable(snapshot.savedSearches);
      trackerRecords = List.unmodifiable(snapshot.trackerRecords);
      final authority = _requiredNormalizedBaseURL(snapshot.baseURL);
      _savedSearchLegacyAuthorityBaseURL = authority;
      _trackerLegacyAuthorityBaseURL = authority;
    }
    _cachedJobDetails = Map.unmodifiable(snapshot.cachedJobDetails);
    updateRuns = List.unmodifiable(snapshot.updateRuns);
    sources = List.unmodifiable(snapshot.sources);
    healthSummary = snapshot.healthSummary;
    cachedJobCount = _cachedAllJobs.length;
    cacheSavedAt = snapshot.savedAt;
    operationalDataLoadedAt = snapshot.operationalDataLoadedAt;
    connectionStatus = 'Offline (cached)';
    connectionMessage = null;
    _syncSavedSearchSequence();
  }

  Future<AtlasLocalCacheStore?> _ensureLocalCacheStore() async {
    if (_localCacheStore != null) {
      return _localCacheStore;
    }
    final factory = _localCacheStoreFactory;
    if (factory == null) {
      return null;
    }
    _localCacheStore = await factory(
      privateStateProtectionActive: () => _privateStateProtectionActive,
    );
    return _localCacheStore;
  }

  Future<void> _writePersistedCache() {
    return _retainCacheMutation(_performPersistedCacheWrite);
  }

  Future<void> _retainCacheMutation(Future<void> Function() body) {
    final previous = _cacheMutationOperation;
    late final Future<void> operation;
    Future<void> run() async {
      if (previous != null) {
        try {
          await previous;
        } catch (_) {
          // A later explicit cache mutation is independent from this failure.
        }
      }
      await body();
    }

    operation = run().whenComplete(() {
      if (identical(_cacheMutationOperation, operation)) {
        _cacheMutationOperation = null;
      }
    });
    _cacheMutationOperation = operation;
    return operation;
  }

  Future<T> _retainCompatibilityPrivateMutation<T>(Future<T> Function() body) {
    final previous = _compatibilityPrivateMutationOperation;
    late final Future<void> retained;
    Future<T> run() async {
      if (previous != null) {
        await previous;
      }
      return body();
    }

    final result = run();
    retained = result
        .then<void>((_) {}, onError: (Object _, StackTrace _) {})
        .whenComplete(() {
          if (identical(_compatibilityPrivateMutationOperation, retained)) {
            _compatibilityPrivateMutationOperation = null;
          }
        });
    _compatibilityPrivateMutationOperation = retained;
    return result;
  }

  Future<void> _performPersistedCacheWrite() async {
    if (_plaintextAuthorityAdmission != null &&
        !_privateStateProtectionActive) {
      try {
        await _runLegacyPrivateOperation(_performPersistedCacheWriteAdmitted);
      } on AtlasVaultPlaintextAuthorityAdmissionException {
        return;
      }
      return;
    }
    await _performPersistedCacheWriteAdmitted();
  }

  Future<void> _performPersistedCacheWriteAdmitted() async {
    if (_plaintextMigrationBlocksPersistedCacheWrites) {
      return;
    }
    final store = await _ensureLocalCacheStore();
    final savedAt = cacheSavedAt;
    if (store == null ||
        savedAt == null ||
        _plaintextMigrationBlocksPersistedCacheWrites) {
      return;
    }
    final searchRequest =
        _committedPublicSearchRequest ?? _cacheSearchRequest();
    final includesLegacyPrivateState =
        !_privateStateProtectionActive &&
        _legacyPrivateProjectionMatchesAuthority(baseURL);
    var snapshot = AtlasLocalCacheSnapshot(
      schemaVersion: AtlasLocalCacheSnapshot.currentSchemaVersion,
      baseURL: baseURL,
      savedAt: savedAt,
      searchRequest: searchRequest,
      searchResponse: AtlasSearchResponse(
        total: total,
        limit: results.length,
        offset: 0,
        results: results,
        facets: facets,
        facetLabels: facetLabels,
        unclassifiedCount: unclassifiedCount,
      ),
      cachedAllJobs: _cachedAllJobs.isEmpty ? results : _cachedAllJobs,
      healthSummary: healthSummary,
      savedSearches: includesLegacyPrivateState
          ? savedSearches
          : const <AtlasSavedSearch>[],
      trackerRecords: includesLegacyPrivateState
          ? trackerRecords
          : const <AtlasApplicationRecord>[],
      cachedJobDetails: _cachedJobDetails,
      updateRuns: updateRuns,
      sources: sources,
      operationalDataLoadedAt: operationalDataLoadedAt,
    );
    if (_plaintextMigrationBlocksPersistedCacheWrites) {
      return;
    }
    if (_privateStateProtectionActive) {
      snapshot = snapshot.withoutPrivateState();
    }
    if (_plaintextMigrationBlocksPersistedCacheWrites) {
      return;
    }
    await store.write(snapshot);
  }

  Future<T> _runLegacyPrivateOperation<T>(
    Future<T> Function() operation,
  ) async {
    final admission = _plaintextAuthorityAdmission;
    if (admission == null) {
      return operation();
    }
    try {
      return await admission.runLegacyPrivateOperation(() async {
        _crossProcessPlaintextAuthorityBlocked = false;
        return operation();
      });
    } on AtlasVaultPlaintextAuthorityAdmissionException {
      _crossProcessPlaintextAuthorityBlocked = true;
      connectionMessage = _plaintextAuthorityUnavailableMessage;
      _hideLegacyPrivateStateForMigration();
      throw const AtlasVaultPlaintextAuthorityAdmissionException();
    }
  }

  Future<void> _drainPlaintextOperationsForActivation(
    int activationGeneration,
    Uri activationAuthority,
  ) async {
    await drainAdmittedPlaintextOperations();
    _requireCurrentPrivateActivation(activationGeneration, activationAuthority);
  }

  Future<void> _drainCacheWriteForMigration() async {
    final operation = _cacheMutationOperation;
    if (operation == null) {
      return;
    }
    try {
      await operation;
    } catch (_) {
      // Migration re-reads the authoritative cache after the admitted write.
    }
  }

  @override
  Future<void> drainAdmittedPlaintextOperations() async {
    while (true) {
      final compatibility = _compatibilityPrivateMutationOperation;
      final cache = _cacheMutationOperation;
      if (compatibility == null && cache == null) {
        return;
      }
      if (compatibility != null) {
        await compatibility;
      }
      if (cache != null) {
        try {
          await cache;
        } catch (_) {
          // Migration re-reads every plaintext source after admitted work.
        }
      }
    }
  }

  @override
  Future<void> beginRecoveryImportAdmission() async {
    if (_recoveryImportAdmissionInProgress) {
      throw const AtlasVaultInteroperabilityException();
    }
    _recoveryImportAdmissionInProgress = true;
    _privateAuthorityGeneration += 1;
    try {
      await drainAdmittedPlaintextOperations();
    } catch (_) {
      _recoveryImportAdmissionInProgress = false;
      rethrow;
    }
  }

  @override
  void endRecoveryImportAdmission() {
    _recoveryImportAdmissionInProgress = false;
  }

  void _installPrivateSnapshot(AtlasVaultPrivateStateSnapshot snapshot) {
    savedSearches = List<AtlasSavedSearch>.unmodifiable(snapshot.savedSearches);
    trackerRecords = List<AtlasApplicationRecord>.unmodifiable(
      snapshot.trackerRecords,
    );
    _clearLegacyPrivateProjectionAuthority();
    _syncSavedSearchSequence();
  }

  void _hideLegacyPrivateStateForMigration() {
    _privateAuthorityGeneration += 1;
    savedSearches = const <AtlasSavedSearch>[];
    trackerRecords = const <AtlasApplicationRecord>[];
    _clearLegacyPrivateProjectionAuthority();
    _syncSavedSearchSequence();
    notifyListeners();
  }

  void _requireCurrentLegacyRollbackRestoration(
    int generation,
    Uri expectedAuthority,
  ) {
    final context = _plaintextMigrationContext;
    if (generation != _privateAuthorityGeneration ||
        context == null ||
        context.owner.status !=
            AtlasVaultPlaintextMigrationPresentationStatus.restoringLegacy ||
        _privateActivationInProgress ||
        _privateDeactivationInProgress ||
        (_privateStatePersistence?.isActive ?? false) ||
        _requiredNormalizedBaseURL(baseURL) != expectedAuthority) {
      throw const AtlasVaultPlaintextMigrationException();
    }
  }

  Uri _requiredNormalizedBaseURL(Uri? value) {
    final normalized = value == null
        ? null
        : AtlasAPIClient.normalizedBaseURL(value.toString());
    if (normalized == null) {
      throw const AtlasVaultPlaintextMigrationException();
    }
    return normalized;
  }

  _AtlasConnectionOperation _beginConnectionOperation(Uri sourceAuthority) {
    final operation = _AtlasConnectionOperation(
      identifier: ++_connectionOperationSequence,
      sourceAuthority: _requiredNormalizedBaseURL(sourceAuthority),
    );
    _activeConnectionOperation = operation;
    return operation;
  }

  bool _isCurrentConnectionOperation(_AtlasConnectionOperation operation) =>
      identical(_activeConnectionOperation, operation);

  void _requireCurrentConnectionOperation(
    _AtlasConnectionOperation operation,
    AtlasAPIClient _,
  ) {
    if (!_isCurrentConnectionOperation(operation)) {
      throw const _AtlasStaleConnectionOperation();
    }
  }

  void _requireCurrentCompatibilityConnectionOperation(
    _AtlasConnectionOperation operation,
    AtlasAPIClient client,
  ) {
    _requireCurrentConnectionOperation(operation, client);
    final clientAuthority = AtlasAPIClient.normalizedBaseURL(
      client.baseURL.toString(),
    );
    if (clientAuthority != operation.sourceAuthority) {
      throw const _AtlasStaleConnectionOperation();
    }
  }

  void _completeConnectionOperation(_AtlasConnectionOperation operation) {
    if (identical(_activeConnectionOperation, operation)) {
      _activeConnectionOperation = null;
    }
  }

  Future<void> _preparePrivateAuthorityChange(
    _AtlasConnectionOperation operation,
    AtlasAPIClient client,
  ) async {
    _requireCurrentConnectionOperation(operation, client);
    if (_requiredNormalizedBaseURL(baseURL) == operation.sourceAuthority) {
      return;
    }
    if (_privateActivationInProgress ||
        _recoveryImportAdmissionInProgress ||
        _recoveryImportBlocksLegacyPrivateAuthority ||
        (_plaintextMigrationContext?.owner.blocksLegacyPrivateAuthority ??
            false)) {
      throw const AtlasVaultPrivateStateException();
    }
    final deactivation = _privateDeactivationOperation;
    if (deactivation != null) {
      await deactivation;
      _requireCurrentConnectionOperation(operation, client);
    }
    if (_privateStatePersistence?.isActive ?? false) {
      await deactivateAtlasVault();
      _requireCurrentConnectionOperation(operation, client);
    }
    if (_privateStateProtectionActive) {
      throw const AtlasVaultPrivateStateException();
    }
  }

  Uri? _legacyPrivateProjectionAuthority() {
    final savedAuthority = savedSearches.isEmpty
        ? null
        : _savedSearchLegacyAuthorityBaseURL;
    final trackerAuthority = trackerRecords.isEmpty
        ? null
        : _trackerLegacyAuthorityBaseURL;
    if ((savedSearches.isNotEmpty && savedAuthority == null) ||
        (trackerRecords.isNotEmpty && trackerAuthority == null)) {
      throw const AtlasVaultPlaintextMigrationException();
    }
    if (savedAuthority != null &&
        trackerAuthority != null &&
        savedAuthority != trackerAuthority) {
      throw const AtlasVaultPlaintextMigrationException();
    }
    return savedAuthority ?? trackerAuthority;
  }

  void _installLegacyPrivateProjection({
    required List<AtlasSavedSearch> savedSearches,
    required List<AtlasApplicationRecord> trackerRecords,
    required Uri authorityBaseURL,
  }) {
    final authority = _requiredNormalizedBaseURL(authorityBaseURL);
    this.savedSearches = List<AtlasSavedSearch>.unmodifiable(savedSearches);
    this.trackerRecords = List<AtlasApplicationRecord>.unmodifiable(
      trackerRecords,
    );
    _savedSearchLegacyAuthorityBaseURL = authority;
    _trackerLegacyAuthorityBaseURL = authority;
    _syncSavedSearchSequence();
  }

  void _clearLegacyPrivateProjectionAuthority() {
    _savedSearchLegacyAuthorityBaseURL = null;
    _trackerLegacyAuthorityBaseURL = null;
  }

  bool _legacyPrivateProjectionMatchesAuthority(Uri authorityBaseURL) {
    if (savedSearches.isEmpty && trackerRecords.isEmpty) {
      return true;
    }
    try {
      return _legacyPrivateProjectionAuthority() ==
          _requiredNormalizedBaseURL(authorityBaseURL);
    } catch (_) {
      return false;
    }
  }

  void _requireLegacySavedSearchMutationAuthority() {
    _requireLegacyPrivateProjectionMutationAuthority();
  }

  void _requireLegacyTrackerMutationAuthority() {
    _requireLegacyPrivateProjectionMutationAuthority();
  }

  void _requireLegacyPrivateProjectionMutationAuthority() {
    final authority = _legacyPrivateProjectionAuthority();
    if (authority != null && authority != _requiredNormalizedBaseURL(baseURL)) {
      throw const AtlasVaultPrivateStateException();
    }
  }

  bool _mayAcceptCompatibilityMutation(int authorityGeneration) {
    return !_privateActivationInProgress &&
        !_privateDeactivationInProgress &&
        !_privateStateProtectionActive &&
        _privateAuthorityGeneration == authorityGeneration;
  }

  bool _mayAcceptPrivateRead(
    AtlasVaultPrivateStatePersistence persistence,
    int authorityGeneration,
  ) {
    return !_privateActivationInProgress &&
        !_privateDeactivationInProgress &&
        persistence.isActive &&
        _privateAuthorityGeneration == authorityGeneration;
  }

  void _requireCurrentPrivateActivation(
    int authorityGeneration,
    Uri expectedAuthority,
  ) {
    if (!_privateActivationInProgress ||
        _privateDeactivationInProgress ||
        _privateAuthorityGeneration != authorityGeneration ||
        _requiredNormalizedBaseURL(baseURL) != expectedAuthority) {
      throw const AtlasVaultPrivateStateException();
    }
  }

  void _upsertSavedSearch(AtlasSavedSearch savedSearch) {
    final remaining = savedSearches
        .where((existing) => existing.name != savedSearch.name)
        .toList(growable: false);
    savedSearches = List.unmodifiable([savedSearch, ...remaining]);
    _syncSavedSearchSequence();
  }

  void _upsertTrackerRecord(AtlasApplicationRecord record) {
    final remaining = trackerRecords
        .where((existing) => existing.jobKey != record.jobKey)
        .toList(growable: false);
    trackerRecords = List.unmodifiable([record, ...remaining]);
  }
}

enum _FilterAvailabilityScope {
  city,
  country,
  contract,
  seniority,
  grade,
  ccog,
  organization,
  workMode,
  capability,
  unv;

  List<String> get facetKeys {
    return switch (this) {
      _FilterAvailabilityScope.city => const <String>['cities'],
      _FilterAvailabilityScope.country => const <String>['countries'],
      _FilterAvailabilityScope.contract => const <String>[
        'contract_groups',
        'volunteer_kinds',
      ],
      _FilterAvailabilityScope.seniority => const <String>['seniority_groups'],
      _FilterAvailabilityScope.grade => const <String>['grades'],
      _FilterAvailabilityScope.ccog => const <String>['ccog_families'],
      _FilterAvailabilityScope.organization => const <String>['organizations'],
      _FilterAvailabilityScope.workMode => const <String>['work_modalities'],
      _FilterAvailabilityScope.capability => const <String>['capability_tags'],
      _FilterAvailabilityScope.unv => const <String>[
        'unv_categories',
        'unv_volunteer_types',
      ],
    };
  }
}

AtlasSearchFilters _clearedFilters(
  AtlasSearchFilters filters,
  _FilterAvailabilityScope scope,
) {
  return switch (scope) {
    _FilterAvailabilityScope.city => filters.copyWith(city: ''),
    _FilterAvailabilityScope.country => filters.copyWith(countryISO3: ''),
    _FilterAvailabilityScope.contract => filters.copyWith(
      contractGroups: <String>{},
      volunteerKinds: <String>{},
      unvCategories: <String>{},
      unvVolunteerTypes: <String>{},
    ),
    _FilterAvailabilityScope.seniority => filters.copyWith(
      seniorityGroups: <String>{},
    ),
    _FilterAvailabilityScope.grade => filters.copyWith(gradeCodes: <String>{}),
    _FilterAvailabilityScope.ccog => filters.copyWith(ccogFamilies: <String>{}),
    _FilterAvailabilityScope.organization => filters.copyWith(
      organizations: <String>{},
    ),
    _FilterAvailabilityScope.workMode => filters.copyWith(
      workModalities: <String>{},
    ),
    _FilterAvailabilityScope.capability => filters.copyWith(
      capabilityTags: <String>{},
      capabilityQuery: '',
    ),
    _FilterAvailabilityScope.unv => filters.copyWith(
      unvCategories: <String>{},
      unvVolunteerTypes: <String>{},
    ),
  };
}

List<AtlasFacetOption> _facetOptions(
  Map<String, int> values,
  Map<String, String> labels, {
  required int limit,
}) {
  final entries = values.entries.toList()
    ..sort((left, right) {
      if (left.value == right.value) {
        return left.key.compareTo(right.key);
      }
      return right.value.compareTo(left.value);
    });
  return entries
      .take(limit)
      .map(
        (entry) => AtlasFacetOption(
          id: entry.key,
          title: labels[entry.key] ?? displayAtlasFilterValue(entry.key),
          count: entry.value,
        ),
      )
      .toList(growable: false);
}

void _incrementFacet(
  Map<String, Map<String, int>> output, {
  required String key,
  required String? value,
  bool skip = false,
}) {
  final clean = value?.trim();
  if (skip || clean == null || clean.isEmpty) {
    return;
  }
  output.putIfAbsent(key, () => <String, int>{});
  output[key]![clean] = (output[key]![clean] ?? 0) + 1;
}

Map<String, Map<String, String>> _mergeFacetLabels(
  Map<String, Map<String, String>> first,
  Map<String, Map<String, String>> second,
) {
  final merged = <String, Map<String, String>>{};
  for (final source in [first, second]) {
    for (final entry in source.entries) {
      merged.putIfAbsent(entry.key, () => <String, String>{});
      merged[entry.key]!.addAll(entry.value);
    }
  }
  return merged;
}

bool _searchRequestsEquivalent(
  AtlasSearchRequest left,
  AtlasSearchRequest right,
) {
  return left.text == right.text &&
      _listEquals(left.status, right.status) &&
      _listEquals(left.organizations, right.organizations) &&
      _listEquals(left.sourceIDs, right.sourceIDs) &&
      _listEquals(left.cities, right.cities) &&
      _listEquals(left.countriesISO3, right.countriesISO3) &&
      _listEquals(left.nationalInternational, right.nationalInternational) &&
      _listEquals(left.gradeCodes, right.gradeCodes) &&
      _listEquals(left.ccogFamilies, right.ccogFamilies) &&
      _listEquals(left.capabilityTags, right.capabilityTags) &&
      _listEquals(left.contractGroups, right.contractGroups) &&
      _listEquals(left.seniorityGroups, right.seniorityGroups) &&
      _listEquals(left.workModalities, right.workModalities) &&
      _listEquals(left.volunteerKinds, right.volunteerKinds) &&
      _listEquals(left.unvCategories, right.unvCategories) &&
      _listEquals(left.unvVolunteerTypes, right.unvVolunteerTypes) &&
      left.closingDateTo == right.closingDateTo &&
      left.includeLowConfidence == right.includeLowConfidence &&
      left.sort == right.sort;
}

bool _listEquals(List<String> left, List<String> right) {
  if (left.length != right.length) {
    return false;
  }
  for (var index = 0; index < left.length; index += 1) {
    if (left[index] != right[index]) {
      return false;
    }
  }
  return true;
}

bool _jobMatchesCity(JobSearchResult job, String city) {
  final jobCity = job.city?.toLowerCase();
  return jobCity == city || job.dutyStation.toLowerCase().contains(city);
}

bool _jobMatchesCountry(JobSearchResult job, String country) {
  final jobCountry = job.countryISO3?.toUpperCase();
  return jobCountry == country ||
      job.dutyStation.toUpperCase().contains(country);
}

String _localScopeValue(JobSearchResult job) {
  final explicit = job.nationalInternational;
  if (explicit != null && explicit.trim().isNotEmpty) {
    return _normalizedToken(explicit);
  }
  final grade = _normalizedGrade(job.gradeCode);
  if (grade.startsWith('P') ||
      grade.startsWith('D') ||
      grade.startsWith('IPSA')) {
    return 'international';
  }
  if (grade.startsWith('NO') ||
      grade.startsWith('NPSA') ||
      grade.startsWith('G')) {
    return 'national';
  }
  return 'unknown';
}

Set<String> _localContractTokens(JobSearchResult job) {
  return <String?>{
    job.contractGroup,
    job.contractCategory,
    job.contractLabel,
  }.nonNulls.map(_normalizedToken).where((value) => value.isNotEmpty).toSet();
}

Set<String> _localVolunteerTokens(JobSearchResult job) {
  final text = _localSearchText(job);
  if (text.contains('un volunteer') || job.sourceID == 'unv_uvp') {
    return const <String>{'un_volunteer'};
  }
  if (text.contains('volunteer')) {
    return const <String>{'volunteer'};
  }
  return const <String>{};
}

String _localSeniorityValue(JobSearchResult job) {
  final fallback = _normalizedToken(job.seniorityGroup ?? '');
  final standard = _standardTierToSeniority(job.standardSeniorityTier);
  if (standard == null) {
    return fallback;
  }
  if (standard == 'ungraded_nonstaff_or_pathway' &&
      (fallback == 'volunteer' ||
          fallback == 'generic_volunteer' ||
          fallback == 'internship_trainee')) {
    return fallback;
  }
  return standard;
}

String? _standardTierToSeniority(String? value) {
  final tier = value?.trim().toUpperCase();
  if (tier == null || tier.isEmpty) {
    return null;
  }
  if (tier.contains('T1_ENTRY') || tier.contains('T2_JUNIOR')) {
    return 'entry_junior';
  }
  if (tier.contains('T3_MID')) {
    return 'mid';
  }
  if (tier.contains('T4_SENIOR') || tier.contains('T5_PRINCIPAL')) {
    return 'senior';
  }
  if (tier.contains('T6') || tier.contains('T7') || tier.contains('DIRECTOR')) {
    return 'director_executive';
  }
  if (tier.contains('UNGRADED') || tier.contains('PATHWAY')) {
    return 'ungraded_nonstaff_or_pathway';
  }
  return 'unknown';
}

bool _isClosingSoon(JobSearchResult job) {
  final closing = job.closingDate;
  if (closing == null) {
    return false;
  }
  final now = DateTime.now();
  final soon = now.add(const Duration(days: 7));
  return !closing.isBefore(now) && !closing.isAfter(soon);
}

bool _isDeadlinePast(JobSearchResult job, {required DateTime now}) {
  final closing = job.closingDate;
  return closing != null && closing.isBefore(now);
}

bool _isUnknownGrade(String value) {
  final normalized = _normalizedGrade(value);
  return normalized.isEmpty || normalized.contains('UNKNOWN');
}

String _normalizedGrade(String value) {
  return value.trim().replaceAll('-', '').replaceAll(' ', '').toUpperCase();
}

String _normalizedToken(String value) {
  return value.trim().toLowerCase().replaceAll(' ', '_');
}

String _localSearchText(JobSearchResult job) {
  return <String?>[
    job.title,
    job.organization,
    job.sourceID,
    job.dutyStation,
    job.city,
    job.countryISO3,
    job.gradeCode,
    job.contractLabel,
    job.contractCategory,
    job.contractGroup,
    job.seniorityGroup,
    job.standardSeniorityTier,
    job.workModality,
    job.ccogFamilyCode,
    job.ccogFamilyLabel,
    job.ccogPrimaryCode,
    job.ccogPrimaryLabel,
    job.capabilityTags.join(' '),
    job.description,
  ].nonNulls.join(' ').toLowerCase();
}

int _compareNullableDates(
  DateTime? left,
  DateTime? right,
  String leftTitle,
  String rightTitle,
) {
  if (left != null && right != null) {
    return left.compareTo(right);
  }
  if (left == null && right != null) {
    return 1;
  }
  if (left != null && right == null) {
    return -1;
  }
  return leftTitle.compareTo(rightTitle);
}

AtlasSearchFilters _filtersFromRequest(AtlasSearchRequest request) {
  return AtlasSearchFilters(
    openOnly: request.status.contains('open'),
    city: request.cities.join(', '),
    countryISO3: request.countriesISO3.join(', '),
    scope: _scopeFromAPIValues(request.nationalInternational),
    includeLowConfidence: request.includeLowConfidence,
    closingSoon: request.closingDateTo != null,
    gradeCodes: request.gradeCodes.toSet(),
    workModalities: request.workModalities.toSet(),
    sourceIDs: request.sourceIDs.toSet(),
    organizations: request.organizations.toSet(),
    ccogFamilies: request.ccogFamilies.toSet(),
    contractGroups: request.contractGroups.toSet(),
    seniorityGroups: request.seniorityGroups.toSet(),
    volunteerKinds: request.volunteerKinds.toSet(),
    unvCategories: request.unvCategories.toSet(),
    unvVolunteerTypes: request.unvVolunteerTypes.toSet(),
    capabilityTags: request.capabilityTags.toSet(),
  );
}

AtlasScopeFilter _scopeFromAPIValues(List<String> values) {
  final valueSet = values.toSet();
  for (final scope in AtlasScopeFilter.values) {
    if (_stringSetEquals(valueSet, scope.apiValues.toSet())) {
      return scope;
    }
  }
  if (valueSet.contains('international')) {
    return AtlasScopeFilter.international;
  }
  if (valueSet.contains('national') || valueSet.contains('local')) {
    return AtlasScopeFilter.national;
  }
  if (valueSet.contains('unknown')) {
    return AtlasScopeFilter.unspecified;
  }
  return AtlasScopeFilter.any;
}

bool _stringSetEquals(Set<String> left, Set<String> right) {
  if (left.length != right.length) {
    return false;
  }
  return left.containsAll(right);
}

String _healthMessage(AtlasHealthSummary health) {
  final pieces = <String>['Connected: ${health.status}'];
  if (health.openJobs != null) {
    pieces.add('${health.openJobs} open jobs');
  }
  if (health.enabledSources != null) {
    pieces.add('${health.enabledSources} enabled sources');
  }
  return '${pieces.join(', ')}.';
}

String _formatBaseURL(Uri uri) {
  final userInfo = uri.userInfo.isEmpty ? '' : '${uri.userInfo}@';
  final port = uri.hasPort && uri.port != 0 ? ':${uri.port}' : '';
  return '${uri.scheme}://$userInfo${uri.host}$port';
}

String _formatSavedAt(DateTime value) {
  final local = value.toLocal();
  final date =
      '${local.year.toString().padLeft(4, '0')}-'
      '${local.month.toString().padLeft(2, '0')}-'
      '${local.day.toString().padLeft(2, '0')}';
  final time =
      '${local.hour.toString().padLeft(2, '0')}:'
      '${local.minute.toString().padLeft(2, '0')}';
  return '$date $time';
}

String _formatCount(int value) {
  final sign = value < 0 ? '-' : '';
  final digits = value.abs().toString();
  final buffer = StringBuffer();
  for (var index = 0; index < digits.length; index += 1) {
    final remaining = digits.length - index;
    if (index > 0 && remaining % 3 == 0) {
      buffer.write(',');
    }
    buffer.write(digits[index]);
  }
  return '$sign$buffer';
}

String _compactTimestamp(String value) {
  final parsed = DateTime.tryParse(value);
  if (parsed == null) {
    return value;
  }
  return _formatSavedAt(parsed);
}

String _humanSourceName(String value) {
  final cleaned = value.trim().replaceAll(RegExp(r'[_-]+'), ' ');
  if (cleaned.isEmpty) {
    return 'Unknown source';
  }
  return cleaned
      .split(RegExp(r'\s+'))
      .where((part) => part.isNotEmpty)
      .map((part) {
        final lower = part.toLowerCase();
        if (lower.length <= 4) {
          return lower.toUpperCase();
        }
        return '${lower[0].toUpperCase()}${lower.substring(1)}';
      })
      .join(' ');
}

bool _sourceNeedsAttention(AtlasSourceSummary source) {
  final status = source.healthStatus?.toLowerCase();
  final failures = source.detailFailed ?? 0;
  return status != null && status != 'ok' ||
      failures > 0 ||
      source.missingTransitionAllowed == false;
}

String _firstNonEmpty(Iterable<String?> values) {
  for (final value in values) {
    final trimmed = value?.trim();
    if (trimmed != null && trimmed.isNotEmpty) {
      return trimmed;
    }
  }
  return '';
}

String? _sectionBody(AtlasJobDetail? detail, String title) {
  if (detail == null) {
    return null;
  }
  final target = title.toLowerCase();
  for (final section in detail.displaySections) {
    if (section.title.toLowerCase() == target) {
      return section.body;
    }
  }
  return null;
}

List<AtlasDetailSection> _contentSections(AtlasJobDetail? detail) {
  if (detail == null) {
    return const <AtlasDetailSection>[];
  }
  return detail.displaySections
      .where((section) => !_isFullDescription(section))
      .where((section) => !_isDiagnosticSection(section))
      .toList(growable: false);
}

List<AtlasDetailSection> _diagnosticSections(AtlasJobDetail? detail) {
  if (detail == null) {
    return const <AtlasDetailSection>[];
  }
  return detail.displaySections
      .where(_isDiagnosticSection)
      .toList(growable: false);
}

bool _isFullDescription(AtlasDetailSection section) {
  return section.title.trim().toLowerCase() == 'full description';
}

bool _isDiagnosticSection(AtlasDetailSection section) {
  final title = section.title.trim().toLowerCase();
  return title == 'job record' ||
      title == 'classification' ||
      title == 'locations' ||
      title == 'source features' ||
      title.contains('raw source') ||
      title.contains('raw record') ||
      title.contains('source data') ||
      title.contains('diagnostic') ||
      title.contains('evidence');
}

String? _detailQualityStatus(AtlasJobDetail? detail) {
  if (detail == null) {
    return null;
  }
  for (final section in detail.displaySections) {
    for (final row in section.rows) {
      if (row.label.trim().toLowerCase() == 'detail quality status') {
        return row.value;
      }
    }
  }
  return null;
}

String _displayScope(String? value) {
  final raw = value?.trim();
  if (raw == null || raw.isEmpty) {
    return 'Scope unknown';
  }
  return _humanSourceName(raw);
}

String _jobWord(int count) => count == 1 ? 'job' : 'jobs';

Color _sourceColor(String value) {
  const mask64 = 0xffffffffffffffff;
  var seed = 0;
  for (final scalar in value.runes) {
    seed = ((seed * 31) + scalar) & mask64;
  }
  final hue = (seed % 360).toDouble();
  return HSVColor.fromAHSV(1, hue, 0.52, 0.58).toColor();
}

class AtlasApp extends StatelessWidget {
  const AtlasApp({super.key});

  @override
  Widget build(BuildContext context) {
    final scheme = ColorScheme.fromSeed(
      seedColor: AtlasPalette.accent,
      brightness: Brightness.light,
    );

    return MaterialApp(
      title: 'Atlas',
      debugShowCheckedModeBanner: false,
      theme: ThemeData(
        useMaterial3: true,
        colorScheme: scheme.copyWith(
          primary: AtlasPalette.accent,
          secondary: AtlasPalette.strategyOrange,
          error: AtlasPalette.deadlineRed,
        ),
        scaffoldBackgroundColor: AtlasPalette.background,
        appBarTheme: const AppBarTheme(
          centerTitle: false,
          elevation: 0,
          backgroundColor: AtlasPalette.background,
          foregroundColor: AtlasPalette.ink,
          surfaceTintColor: Colors.transparent,
          titleTextStyle: TextStyle(
            color: AtlasPalette.ink,
            fontSize: 20,
            fontWeight: FontWeight.w700,
          ),
        ),
        navigationBarTheme: NavigationBarThemeData(
          backgroundColor: Colors.white,
          indicatorColor: AtlasPalette.accent.withValues(alpha: 0.14),
          labelTextStyle: WidgetStateProperty.resolveWith(
            (states) => TextStyle(
              fontSize: 12,
              fontWeight: states.contains(WidgetState.selected)
                  ? FontWeight.w700
                  : FontWeight.w500,
            ),
          ),
        ),
        inputDecorationTheme: InputDecorationTheme(
          filled: true,
          fillColor: Colors.white,
          border: OutlineInputBorder(
            borderRadius: BorderRadius.circular(8),
            borderSide: const BorderSide(color: AtlasPalette.border),
          ),
          enabledBorder: OutlineInputBorder(
            borderRadius: BorderRadius.circular(8),
            borderSide: const BorderSide(color: AtlasPalette.border),
          ),
          focusedBorder: OutlineInputBorder(
            borderRadius: BorderRadius.circular(8),
            borderSide: const BorderSide(
              color: AtlasPalette.accent,
              width: 1.5,
            ),
          ),
        ),
      ),
      home: const AtlasHomeShell(),
    );
  }
}

class AtlasHomeShell extends StatefulWidget {
  const AtlasHomeShell({super.key, this.controller});

  final AtlasAppController? controller;

  @override
  State<AtlasHomeShell> createState() => _AtlasHomeShellState();
}

class _AtlasHomeShellState extends State<AtlasHomeShell> {
  AtlasMobileTab _selectedTab = AtlasMobileTab.search;
  late final AtlasAppController _controller;
  late final bool _ownsController;
  AtlasVaultPlaintextMigrationPresentationOwner? _ownedMigrationOwner;
  AtlasVaultInteroperabilityPresentationOwner? _ownedInteroperabilityOwner;
  AtlasVaultTrustedPairingPresentationOwner? _ownedPairingOwner;

  @override
  void initState() {
    super.initState();
    final suppliedController = widget.controller;
    if (suppliedController != null) {
      _controller = suppliedController;
      _ownsController = false;
    } else {
      final assembly = _buildDefaultControllerAssembly();
      _controller = assembly.controller;
      _ownedMigrationOwner = assembly.migrationOwner;
      _ownedInteroperabilityOwner = assembly.interoperabilityOwner;
      _ownedPairingOwner = assembly.pairingOwner;
      _ownsController = true;
    }
    if (_ownsController) {
      WidgetsBinding.instance.addPostFrameCallback((_) {
        if (mounted) {
          unawaited(
            _controller.bootstrapPrivateAuthorityAndLoadPersistedCache(),
          );
        }
      });
    }
  }

  @override
  void dispose() {
    if (_ownsController) {
      unawaited(_ownedInteroperabilityOwner?.stopAndDrain());
      unawaited(_ownedPairingOwner?.stopAndDrain());
      _ownedPairingOwner?.dispose();
      _ownedInteroperabilityOwner?.dispose();
      _ownedMigrationOwner?.dispose();
      _controller.dispose();
    }
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        centerTitle: true,
        title: Text(_selectedTab.title),
        actions: _selectedTab == AtlasMobileTab.search
            ? [
                Padding(
                  padding: const EdgeInsets.only(right: 12),
                  child: AtlasSearchActionGroup(
                    controller: _controller,
                    onShowFilters: _showFilterSheet,
                  ),
                ),
              ]
            : null,
      ),
      body: SafeArea(
        top: false,
        child: IndexedStack(
          index: _selectedTab.index,
          children: [
            AtlasSearchSkeleton(controller: _controller),
            AtlasSavedPanel(controller: _controller),
            AtlasUpdatesPanel(controller: _controller),
            AtlasSourcesPanel(
              controller: _controller,
              onSourceSelected: (source) {
                unawaited(_controller.setSourceFilter(source.sourceID));
                setState(() {
                  _selectedTab = AtlasMobileTab.search;
                });
              },
            ),
            AtlasSettingsPanel(controller: _controller),
          ],
        ),
      ),
      bottomNavigationBar: NavigationBar(
        selectedIndex: _selectedTab.index,
        onDestinationSelected: (index) {
          setState(() {
            _selectedTab = AtlasMobileTab.values[index];
          });
        },
        destinations: const [
          NavigationDestination(
            icon: Icon(AtlasIcons.search),
            selectedIcon: Icon(AtlasIcons.search),
            label: 'Search',
          ),
          NavigationDestination(
            icon: Icon(AtlasIcons.bookmark),
            selectedIcon: Icon(AtlasIcons.bookmarkFilled),
            label: 'Saved',
          ),
          NavigationDestination(
            icon: Icon(AtlasIcons.updates),
            selectedIcon: Icon(AtlasIcons.updates),
            label: 'Updates',
          ),
          NavigationDestination(
            icon: Icon(AtlasIcons.sources),
            selectedIcon: Icon(AtlasIcons.sources),
            label: 'Sources',
          ),
          NavigationDestination(
            icon: Icon(AtlasIcons.settings),
            selectedIcon: Icon(AtlasIcons.settingsFilled),
            label: 'Settings',
          ),
        ],
      ),
    );
  }

  void _showFilterSheet() {
    showModalBottomSheet<void>(
      context: context,
      isScrollControlled: true,
      backgroundColor: Colors.transparent,
      builder: (context) => AtlasFilterSheet(controller: _controller),
    );
  }
}

final class _AtlasDefaultControllerAssembly {
  const _AtlasDefaultControllerAssembly({
    required this.controller,
    this.migrationOwner,
    this.interoperabilityOwner,
    this.pairingOwner,
  });

  final AtlasAppController controller;
  final AtlasVaultPlaintextMigrationPresentationOwner? migrationOwner;
  final AtlasVaultInteroperabilityPresentationOwner? interoperabilityOwner;
  final AtlasVaultTrustedPairingPresentationOwner? pairingOwner;
}

final class _AtlasWindowsTrustedPairingAdmission
    implements
        AtlasVaultPlaintextAuthorityAdmission,
        AtlasVaultRecoveryImportTransactionAdmission,
        AtlasVaultTrustedPairingTransactionAdmission {
  const _AtlasWindowsTrustedPairingAdmission({
    required AtlasWindowsPlaintextAuthorityAdmission base,
    required AtlasVaultPairingTransactionStore pairingTransactionStore,
  }) : // Keep public dependency labels explicit at the assembly boundary.
       // ignore: prefer_initializing_formals
       _base = base,
       // ignore: prefer_initializing_formals
       _pairingTransactionStore = pairingTransactionStore;

  final AtlasWindowsPlaintextAuthorityAdmission _base;
  final AtlasVaultPairingTransactionStore _pairingTransactionStore;

  @override
  Future<T> runLegacyPrivateOperation<T>(Future<T> Function() operation) {
    return _base.runMigrationTransaction(() async {
      final transaction = await _pairingTransactionStore.read();
      try {
        if (transaction != null) {
          throw const AtlasVaultPlaintextAuthorityAdmissionException();
        }
      } finally {
        transaction?.destroy();
      }
      return _base.runLegacyPrivateOperation(operation);
    });
  }

  @override
  Future<T> runMigrationTransaction<T>(Future<T> Function() operation) =>
      _base.runMigrationTransaction(operation);

  @override
  Future<T> runRecoveryImportTransaction<T>(Future<T> Function() operation) =>
      _base.runRecoveryImportTransaction(operation);

  @override
  Future<T> runTrustedPairingTransaction<T>(Future<T> Function() operation) =>
      _base.runMigrationTransaction(operation);
}

final class _AtlasAndroidTrustedPairingAdmission
    implements
        AtlasVaultPlaintextAuthorityAdmission,
        AtlasVaultRecoveryImportTransactionAdmission,
        AtlasVaultTrustedPairingTransactionAdmission {
  _AtlasAndroidTrustedPairingAdmission({
    required AtlasVaultPairingTransactionStore pairingTransactionStore,
    required AtlasVaultProtectedMigrationJournalStore migrationJournalStore,
    required AtlasVaultProtectedRecoveryImportJournalStore
    recoveryImportJournalStore,
    required AtlasVaultSelectedVaultStore selectedVaultStore,
  }) : // Keep public dependency labels explicit at the assembly boundary.
       // ignore: prefer_initializing_formals
       _pairingTransactionStore = pairingTransactionStore,
       // ignore: prefer_initializing_formals
       _migrationJournalStore = migrationJournalStore,
       // ignore: prefer_initializing_formals
       _recoveryImportJournalStore = recoveryImportJournalStore,
       // ignore: prefer_initializing_formals
       _selectedVaultStore = selectedVaultStore;

  static final Object _leaseKey = Object();
  Future<void> _queue = Future<void>.value();

  final AtlasVaultPairingTransactionStore _pairingTransactionStore;
  final AtlasVaultProtectedMigrationJournalStore _migrationJournalStore;
  final AtlasVaultProtectedRecoveryImportJournalStore
  _recoveryImportJournalStore;
  final AtlasVaultSelectedVaultStore _selectedVaultStore;

  @override
  Future<T> runLegacyPrivateOperation<T>(Future<T> Function() operation) {
    return _coordinate(() async {
      AtlasVaultPairingTransaction? pairing;
      Uint8List? migration;
      Uint8List? recoveryImport;
      try {
        pairing = await _pairingTransactionStore.read();
        migration = await _migrationJournalStore.read();
        recoveryImport = await _recoveryImportJournalStore.read();
        final selected = await _selectedVaultStore.read();
        if (pairing != null ||
            migration != null ||
            recoveryImport != null ||
            selected != null) {
          throw const AtlasVaultPlaintextAuthorityAdmissionException();
        }
        return operation();
      } finally {
        pairing?.destroy();
        migration?.fillRange(0, migration.length, 0);
        recoveryImport?.fillRange(0, recoveryImport.length, 0);
      }
    });
  }

  @override
  Future<T> runMigrationTransaction<T>(Future<T> Function() operation) =>
      _coordinate(operation);

  @override
  Future<T> runRecoveryImportTransaction<T>(Future<T> Function() operation) =>
      _coordinate(operation);

  @override
  Future<T> runTrustedPairingTransaction<T>(Future<T> Function() operation) =>
      _coordinate(operation);

  Future<T> _coordinate<T>(Future<T> Function() operation) async {
    if (Zone.current[_leaseKey] == this) {
      return operation();
    }
    final previous = _queue;
    final release = Completer<void>();
    _queue = release.future;
    await previous;
    try {
      return await runZoned(
        operation,
        zoneValues: <Object, Object>{_leaseKey: this},
      );
    } finally {
      release.complete();
    }
  }
}

Future<AtlasVaultPairingCleanInstallDisposition>
_inspectTrustedPairingCleanInstall({
  required AtlasAppController controller,
  required AtlasVaultPrivateStateRuntime runtime,
  required AtlasVaultSelectedVaultStore selectedVaultStore,
  required AtlasVaultProtectedMigrationJournalStore migrationJournalStore,
  required AtlasVaultProtectedRecoveryImportJournalStore
  recoveryImportJournalStore,
  required AtlasLocalCacheMigrationSource cacheSource,
  required AtlasVaultCompatibilityPrivateSource compatibilitySource,
}) async {
  Uint8List? migration;
  Uint8List? recoveryImport;
  try {
    if (runtime.isActive || await selectedVaultStore.read() != null) {
      return AtlasVaultPairingCleanInstallDisposition.existingVault;
    }
    migration = await migrationJournalStore.read();
    recoveryImport = await recoveryImportJournalStore.read();
    if (migration != null || recoveryImport != null) {
      return AtlasVaultPairingCleanInstallDisposition.recoveryRequired;
    }
    if (controller.savedSearches.isNotEmpty ||
        controller.trackerRecords.isNotEmpty) {
      return AtlasVaultPairingCleanInstallDisposition.migrationRequired;
    }
    final cache = await cacheSource.readPrivateStateForMigration();
    if (cache.containsPrivateState) {
      return AtlasVaultPairingCleanInstallDisposition.migrationRequired;
    }
    final compatibility = await compatibilitySource
        .readCompatibilityPrivateState();
    if (compatibility.savedSearches.isNotEmpty ||
        compatibility.trackerRecords.isNotEmpty) {
      return AtlasVaultPairingCleanInstallDisposition.migrationRequired;
    }
    return AtlasVaultPairingCleanInstallDisposition.clean;
  } on AtlasVaultPlaintextAuthorityAdmissionException {
    return AtlasVaultPairingCleanInstallDisposition.recoveryRequired;
  } catch (_) {
    return AtlasVaultPairingCleanInstallDisposition.unavailable;
  } finally {
    migration?.fillRange(0, migration.length, 0);
    recoveryImport?.fillRange(0, recoveryImport.length, 0);
  }
}

AtlasVaultTrustedPairingPresentationOwner _attachWindowsTrustedPairing({
  required AtlasAppController controller,
  required AtlasVaultPrivateStateRuntime runtime,
  required AtlasWindowsVaultSecureKeyStore secureKeyStore,
  required AtlasWindowsVaultLocalStoreIO localStoreIO,
  required AtlasWindowsSelectedVaultStore selectedVaultStore,
  required AtlasWindowsProtectedMigrationJournalStore migrationJournalStore,
  required AtlasWindowsProtectedRecoveryImportJournalStore
  recoveryImportJournalStore,
  required AtlasWindowsPairingTransactionStore transactionStore,
  required AtlasVaultTrustedPairingTransactionAdmission transactionAdmission,
}) {
  final compatibilitySource = _AtlasControllerCompatibilityMigrationSource(
    controller,
  );
  final cacheSource = _AtlasResolvedLocalCacheMigrationSource(() async {
    await controller._drainCacheWriteForMigration();
    return AtlasWindowsDesktopCacheMigrationSource(
      await resolveAtlasPersistentCacheLocation(importLegacyCache: false),
    );
  });
  final coordinator = AtlasVaultTrustedPairingCoordinator(
    identityStore: AtlasWindowsDeviceIdentitySecretStore(),
    registryStore: AtlasWindowsTrustedDeviceRegistryStore(),
    replayStore: AtlasWindowsPairingReplayStore(),
    transactionStore: transactionStore,
    stageStore: AtlasWindowsPairingArtifactStageStore(),
    artifactTransport: AtlasWindowsPairingArtifactTransport(),
    runtime: runtime,
    cleanInstallProbe: () => _inspectTrustedPairingCleanInstall(
      controller: controller,
      runtime: runtime,
      selectedVaultStore: selectedVaultStore,
      migrationJournalStore: migrationJournalStore,
      recoveryImportJournalStore: recoveryImportJournalStore,
      cacheSource: cacheSource,
      compatibilitySource: compatibilitySource,
    ),
    secureKeyStore: secureKeyStore,
    localStoreIO: localStoreIO,
    selectedVaultStore: selectedVaultStore,
    activateInstalledVault: (vaultId) async =>
        await controller._activateImportedAtlasVault(vaultId) ==
        AtlasVaultActivationResult.activated,
    transactionAdmission: transactionAdmission,
  );
  final owner = AtlasVaultTrustedPairingPresentationOwner(
    coordinator: coordinator,
  );
  controller.attachTrustedPairingContext(
    AtlasVaultTrustedPairingContext(owner: owner),
  );
  return owner;
}

AtlasVaultTrustedPairingPresentationOwner _attachAndroidTrustedPairing({
  required AtlasAppController controller,
  required AtlasVaultPrivateStateRuntime runtime,
  required AtlasAndroidVaultSecureKeyStore secureKeyStore,
  required AtlasAndroidVaultLocalStoreIO localStoreIO,
  required AtlasAndroidSelectedVaultStore selectedVaultStore,
  required AtlasAndroidProtectedMigrationJournalStore migrationJournalStore,
  required AtlasAndroidProtectedRecoveryImportJournalStore
  recoveryImportJournalStore,
  required AtlasAndroidPairingTransactionStore transactionStore,
  required AtlasVaultTrustedPairingTransactionAdmission transactionAdmission,
}) {
  final compatibilitySource = _AtlasControllerCompatibilityMigrationSource(
    controller,
  );
  final cacheSource = _AtlasControllerCacheMigrationSource(controller);
  final coordinator = AtlasVaultTrustedPairingCoordinator(
    identityStore: AtlasAndroidDeviceIdentitySecretStore(),
    registryStore: AtlasAndroidTrustedDeviceRegistryStore(),
    replayStore: AtlasAndroidPairingReplayStore(),
    transactionStore: transactionStore,
    stageStore: AtlasAndroidPairingArtifactStageStore(),
    artifactTransport: AtlasAndroidPairingArtifactTransport(),
    runtime: runtime,
    cleanInstallProbe: () => _inspectTrustedPairingCleanInstall(
      controller: controller,
      runtime: runtime,
      selectedVaultStore: selectedVaultStore,
      migrationJournalStore: migrationJournalStore,
      recoveryImportJournalStore: recoveryImportJournalStore,
      cacheSource: cacheSource,
      compatibilitySource: compatibilitySource,
    ),
    secureKeyStore: secureKeyStore,
    localStoreIO: localStoreIO,
    selectedVaultStore: selectedVaultStore,
    activateInstalledVault: (vaultId) async =>
        await controller._activateImportedAtlasVault(vaultId) ==
        AtlasVaultActivationResult.activated,
    transactionAdmission: transactionAdmission,
  );
  final owner = AtlasVaultTrustedPairingPresentationOwner(
    coordinator: coordinator,
  );
  controller.attachTrustedPairingContext(
    AtlasVaultTrustedPairingContext(owner: owner),
  );
  return owner;
}

AtlasVaultPlaintextMigrationPresentationOwner _attachWindowsMigration({
  required AtlasAppController controller,
  required AtlasVaultPrivateStateRuntime runtime,
  required AtlasWindowsVaultSecureKeyStore keyStore,
  required AtlasWindowsVaultLocalStoreIO localStore,
  required AtlasWindowsSelectedVaultStore selectedVaultStore,
  required AtlasWindowsProtectedMigrationJournalStore migrationJournalStore,
  required AtlasVaultPlaintextAuthorityAdmission authorityAdmission,
}) {
  final inMemorySource = _AtlasControllerPlaintextMigrationSource(controller);
  final compatibilitySource = _AtlasControllerCompatibilityMigrationSource(
    controller,
  );
  final cacheSource = _AtlasResolvedLocalCacheMigrationSource(() async {
    await controller._drainCacheWriteForMigration();
    return AtlasWindowsDesktopCacheMigrationSource(
      await resolveAtlasPersistentCacheLocation(),
    );
  });
  final coordinator = AtlasVaultPlaintextMigrationCoordinator(
    profile: AtlasVaultPlaintextMigrationProfile.windows,
    inMemorySource: inMemorySource,
    compatibilitySource: compatibilitySource,
    cacheSource: cacheSource,
    operationAdmission: controller,
    authorityAdmission: authorityAdmission,
    conditionalSavedSearchDelete:
        compatibilitySource.conditionalDeleteSavedSearch,
    conditionalTrackerDelete:
        compatibilitySource.conditionalDeleteTrackerRecord,
    journalStore: migrationJournalStore,
    selectedVaultStore: selectedVaultStore,
    secureKeyStore: keyStore,
    localStoreIO: localStore,
    privateAuthority: _AtlasControllerMigrationPrivateAuthority(
      controller: controller,
      runtime: runtime,
    ),
  );
  final owner = AtlasVaultPlaintextMigrationPresentationOwner(
    coordinator: coordinator,
    legacyPrivateStateRestorer: controller,
  );
  controller.attachPlaintextMigrationContext(
    AtlasVaultPlaintextMigrationContext(
      owner: owner,
      platform: AtlasVaultPlaintextMigrationPresentationPlatform.windows,
    ),
  );
  return owner;
}

AtlasVaultInteroperabilityPresentationOwner _attachWindowsEncryptedBackup({
  required AtlasAppController controller,
  required AtlasVaultPrivateStateRuntime runtime,
  required AtlasWindowsVaultSecureKeyStore secureKeyStore,
  required AtlasWindowsVaultLocalStoreIO localStoreIO,
  required AtlasWindowsSelectedVaultStore selectedVaultStore,
  required AtlasWindowsProtectedMigrationJournalStore migrationJournalStore,
  required AtlasWindowsProtectedRecoveryImportJournalStore
  recoveryImportJournalStore,
  required AtlasVaultRecoveryImportTransactionAdmission authorityAdmission,
}) {
  final documentTransport = AtlasWindowsEncryptedDocumentTransport();
  final inMemorySource = _AtlasControllerPlaintextMigrationSource(controller);
  final compatibilitySource = _AtlasControllerCompatibilityMigrationSource(
    controller,
  );
  final cacheSource = _AtlasResolvedLocalCacheMigrationSource(() async {
    await controller._drainCacheWriteForMigration();
    return AtlasWindowsDesktopCacheMigrationSource(
      await resolveAtlasPersistentCacheLocation(importLegacyCache: false),
    );
  });
  final coordinator = AtlasVaultInteroperabilityCoordinator(
    runtime: runtime,
    selectedVaultStore: selectedVaultStore,
    migrationJournalStore: migrationJournalStore,
    recoveryImportPending: () async {
      final bytes = await recoveryImportJournalStore.read();
      try {
        return bytes != null;
      } finally {
        bytes?.fillRange(0, bytes.length, 0);
      }
    },
    documentTransport: documentTransport,
    recoveryImportJournalStore: recoveryImportJournalStore,
    secureKeyStore: secureKeyStore,
    localStoreIO: localStoreIO,
    inMemorySource: inMemorySource,
    compatibilitySource: compatibilitySource,
    cacheSource: cacheSource,
    importOperationAdmission: controller,
    importTransactionAdmission: authorityAdmission,
    recoveryImportProfile: AtlasVaultRecoveryImportProfile.windows,
    activateImportedVault: (vaultId) async =>
        await controller._activateImportedAtlasVault(vaultId) ==
        AtlasVaultActivationResult.activated,
    recoveryImportPendingDidChange: controller._recoveryImportPendingDidChange,
  );
  final owner = AtlasVaultInteroperabilityPresentationOwner(
    coordinator: coordinator,
    platformProfile: AtlasVaultInteroperabilityPlatformProfile.windows,
  );
  controller.attachInteroperabilityContext(
    AtlasVaultInteroperabilityContext(owner: owner),
  );
  return owner;
}

_AtlasDefaultControllerAssembly _buildDefaultControllerAssembly() {
  if (Platform.isWindows) {
    final keyStore = AtlasWindowsVaultSecureKeyStore();
    final localStore = AtlasWindowsVaultLocalStoreIO();
    final selectedVaultStore = AtlasWindowsSelectedVaultStore();
    final migrationJournalStore = AtlasWindowsProtectedMigrationJournalStore();
    final recoveryImportJournalStore =
        AtlasWindowsProtectedRecoveryImportJournalStore();
    final transactionStore = AtlasWindowsPairingTransactionStore();
    final baseAuthorityAdmission = AtlasWindowsPlaintextAuthorityAdmission(
      locationProvider: () =>
          resolveAtlasPersistentCacheLocation(importLegacyCache: false),
      journalStore: migrationJournalStore,
      recoveryImportJournalStore: recoveryImportJournalStore,
      selectedVaultStore: selectedVaultStore,
    );
    final authorityAdmission = _AtlasWindowsTrustedPairingAdmission(
      base: baseAuthorityAdmission,
      pairingTransactionStore: transactionStore,
    );
    final runtime = AtlasVaultPrivateStateRuntime(
      secureKeyStore: keyStore,
      localStoreIO: localStore,
    );
    late final AtlasAppController controller;
    controller = AtlasAppController(
      localCacheStoreFactory: _defaultCacheStore,
      privateStatePersistence: runtime,
      plaintextAuthorityAdmission: authorityAdmission,
      recoveryImportPending: () async {
        final bytes = await recoveryImportJournalStore.read();
        try {
          return bytes != null;
        } finally {
          bytes?.fillRange(0, bytes.length, 0);
        }
      },
      compatibilityPrivateStateAdmission: () async {
        final privateState = await _AtlasControllerCompatibilityMigrationSource(
          controller,
        ).readCompatibilityPrivateState();
        return privateState.savedSearches.isNotEmpty ||
            privateState.trackerRecords.isNotEmpty;
      },
    );
    final owner = _attachWindowsMigration(
      controller: controller,
      runtime: runtime,
      keyStore: keyStore,
      localStore: localStore,
      selectedVaultStore: selectedVaultStore,
      migrationJournalStore: migrationJournalStore,
      authorityAdmission: authorityAdmission,
    );
    final interoperabilityOwner = _attachWindowsEncryptedBackup(
      controller: controller,
      runtime: runtime,
      secureKeyStore: keyStore,
      localStoreIO: localStore,
      selectedVaultStore: selectedVaultStore,
      migrationJournalStore: migrationJournalStore,
      recoveryImportJournalStore: recoveryImportJournalStore,
      authorityAdmission: authorityAdmission,
    );
    final pairingOwner = _attachWindowsTrustedPairing(
      controller: controller,
      runtime: runtime,
      secureKeyStore: keyStore,
      localStoreIO: localStore,
      selectedVaultStore: selectedVaultStore,
      migrationJournalStore: migrationJournalStore,
      recoveryImportJournalStore: recoveryImportJournalStore,
      transactionStore: transactionStore,
      transactionAdmission: authorityAdmission,
    );
    return _AtlasDefaultControllerAssembly(
      controller: controller,
      migrationOwner: owner,
      interoperabilityOwner: interoperabilityOwner,
      pairingOwner: pairingOwner,
    );
  }

  if (!Platform.isAndroid) {
    return _AtlasDefaultControllerAssembly(
      controller: AtlasAppController(
        localCacheStoreFactory: _defaultCacheStore,
      ),
    );
  }

  final keyStore = AtlasAndroidVaultSecureKeyStore();
  final localStore = AtlasAndroidVaultLocalStoreIO();
  final selectedVaultStore = AtlasAndroidSelectedVaultStore();
  final migrationJournalStore = AtlasAndroidProtectedMigrationJournalStore();
  final recoveryImportJournalStore =
      AtlasAndroidProtectedRecoveryImportJournalStore();
  final transactionStore = AtlasAndroidPairingTransactionStore();
  final authorityAdmission = _AtlasAndroidTrustedPairingAdmission(
    pairingTransactionStore: transactionStore,
    migrationJournalStore: migrationJournalStore,
    recoveryImportJournalStore: recoveryImportJournalStore,
    selectedVaultStore: selectedVaultStore,
  );
  final runtime = AtlasVaultPrivateStateRuntime(
    secureKeyStore: keyStore,
    localStoreIO: localStore,
  );
  final controller = AtlasAppController(
    localCacheStoreFactory: _defaultCacheStore,
    privateStatePersistence: runtime,
    plaintextAuthorityAdmission: authorityAdmission,
    recoveryImportPending: () async {
      final bytes = await recoveryImportJournalStore.read();
      try {
        return bytes != null;
      } finally {
        bytes?.fillRange(0, bytes.length, 0);
      }
    },
  );
  final inMemorySource = _AtlasControllerPlaintextMigrationSource(controller);
  final compatibilitySource = _AtlasControllerCompatibilityMigrationSource(
    controller,
  );
  final cacheSource = _AtlasControllerCacheMigrationSource(controller);
  final coordinator = AtlasVaultPlaintextMigrationCoordinator(
    inMemorySource: inMemorySource,
    compatibilitySource: compatibilitySource,
    cacheSource: cacheSource,
    operationAdmission: controller,
    journalStore: migrationJournalStore,
    selectedVaultStore: selectedVaultStore,
    secureKeyStore: keyStore,
    localStoreIO: localStore,
    privateAuthority: _AtlasControllerMigrationPrivateAuthority(
      controller: controller,
      runtime: runtime,
    ),
  );
  final owner = AtlasVaultPlaintextMigrationPresentationOwner(
    coordinator: coordinator,
    legacyPrivateStateRestorer: controller,
  );
  controller.attachPlaintextMigrationContext(
    AtlasVaultPlaintextMigrationContext(owner: owner),
  );
  final interoperabilityCoordinator = AtlasVaultInteroperabilityCoordinator(
    runtime: runtime,
    selectedVaultStore: selectedVaultStore,
    migrationJournalStore: migrationJournalStore,
    recoveryImportPending: () async {
      final bytes = await recoveryImportJournalStore.read();
      try {
        return bytes != null;
      } finally {
        bytes?.fillRange(0, bytes.length, 0);
      }
    },
    documentTransport: AtlasAndroidEncryptedDocumentTransport(),
    recoveryImportJournalStore: recoveryImportJournalStore,
    secureKeyStore: keyStore,
    localStoreIO: localStore,
    inMemorySource: inMemorySource,
    compatibilitySource: compatibilitySource,
    cacheSource: cacheSource,
    importOperationAdmission: controller,
    importTransactionAdmission: authorityAdmission,
    activateImportedVault: (vaultId) async =>
        await controller._activateImportedAtlasVault(vaultId) ==
        AtlasVaultActivationResult.activated,
    recoveryImportPendingDidChange: controller._recoveryImportPendingDidChange,
  );
  final interoperabilityOwner = AtlasVaultInteroperabilityPresentationOwner(
    coordinator: interoperabilityCoordinator,
  );
  controller.attachInteroperabilityContext(
    AtlasVaultInteroperabilityContext(owner: interoperabilityOwner),
  );
  final pairingOwner = _attachAndroidTrustedPairing(
    controller: controller,
    runtime: runtime,
    secureKeyStore: keyStore,
    localStoreIO: localStore,
    selectedVaultStore: selectedVaultStore,
    migrationJournalStore: migrationJournalStore,
    recoveryImportJournalStore: recoveryImportJournalStore,
    transactionStore: transactionStore,
    transactionAdmission: authorityAdmission,
  );
  return _AtlasDefaultControllerAssembly(
    controller: controller,
    migrationOwner: owner,
    interoperabilityOwner: interoperabilityOwner,
    pairingOwner: pairingOwner,
  );
}

final class _AtlasControllerPlaintextMigrationSource
    implements AtlasVaultPlaintextStateSource {
  const _AtlasControllerPlaintextMigrationSource(this.controller);

  final AtlasAppController controller;

  @override
  Future<AtlasVaultPlaintextPrivateState> readPlaintextPrivateState() async {
    return AtlasVaultPlaintextPrivateState(
      savedSearches: controller.savedSearches,
      trackerRecords: controller.trackerRecords,
      authorityBaseURL: controller._legacyPrivateProjectionAuthority(),
    );
  }
}

final class _AtlasControllerCompatibilityMigrationSource
    implements AtlasVaultCompatibilityPrivateSource {
  const _AtlasControllerCompatibilityMigrationSource(this.controller);

  final AtlasAppController controller;

  AtlasAPIClient get _client => controller._clientFactory(controller.baseURL);

  @override
  Uri get authorityBaseURL => controller.baseURL;

  @override
  Future<AtlasVaultPlaintextPrivateState>
  readCompatibilityPrivateState() async {
    final client = _client;
    final savedSearches = await client.savedSearchesForPlaintextMigration();
    final trackerRecords = await client.trackerRecordsForPlaintextMigration();
    return AtlasVaultPlaintextPrivateState(
      savedSearches: savedSearches,
      trackerRecords: trackerRecords,
    );
  }

  Future<AtlasConditionalDeleteOutcome> conditionalDeleteSavedSearch(
    AtlasSavedSearch expected,
  ) {
    return _client.conditionalDeleteSavedSearch(expected);
  }

  @override
  Future<bool> deleteSavedSearch(String name) {
    return _client.deleteSavedSearch(name);
  }

  Future<AtlasConditionalDeleteOutcome> conditionalDeleteTrackerRecord(
    AtlasApplicationRecord expected,
  ) {
    return _client.conditionalDeleteTrackerRecord(expected);
  }

  @override
  Future<bool> deleteTrackerRecord(String recordId) {
    return _client.deleteTrackerRecord(recordId);
  }

  @override
  String toString() => '_AtlasControllerCompatibilityMigrationSource()';
}

final class _AtlasControllerCacheMigrationSource
    implements AtlasLocalCacheMigrationSource {
  const _AtlasControllerCacheMigrationSource(this.controller);

  final AtlasAppController controller;

  @override
  Future<AtlasLocalCacheMigrationPrivateState>
  readPrivateStateForMigration() async {
    await controller._drainCacheWriteForMigration();
    final store = await controller._ensureLocalCacheStore();
    if (store == null) {
      throw const AtlasVaultPlaintextMigrationException();
    }
    return store.readPrivateStateForMigration();
  }

  @override
  Future<void> removePrivateStateForMigration({
    required String expectedPrivateSha256,
  }) async {
    await controller._drainCacheWriteForMigration();
    final store = await controller._ensureLocalCacheStore();
    if (store == null) {
      throw const AtlasVaultPlaintextMigrationException();
    }
    await store.removePrivateStateForMigration(
      expectedPrivateSha256: expectedPrivateSha256,
    );
  }
}

final class _AtlasResolvedLocalCacheMigrationSource
    implements
        AtlasLocalCacheMigrationSource,
        AtlasLocalCacheMigrationCleanupSource {
  const _AtlasResolvedLocalCacheMigrationSource(this._resolve);

  final Future<AtlasLocalCacheMigrationSource> Function() _resolve;

  @override
  Future<AtlasLocalCacheMigrationPrivateState>
  readPrivateStateForMigration() async {
    return (await _resolve()).readPrivateStateForMigration();
  }

  @override
  Future<void> removePrivateStateForMigration({
    required String expectedPrivateSha256,
  }) async {
    await (await _resolve()).removePrivateStateForMigration(
      expectedPrivateSha256: expectedPrivateSha256,
    );
  }

  @override
  Future<void> completePrivateStateCleanupForMigration({
    required String? expectedPrivateSha256,
  }) async {
    final source = await _resolve();
    if (source is! AtlasLocalCacheMigrationCleanupSource) {
      throw const AtlasVaultPlaintextMigrationException();
    }
    await (source as AtlasLocalCacheMigrationCleanupSource)
        .completePrivateStateCleanupForMigration(
          expectedPrivateSha256: expectedPrivateSha256,
        );
  }
}

final class _AtlasControllerMigrationPrivateAuthority
    implements AtlasVaultPlaintextMigrationPrivateAuthority {
  const _AtlasControllerMigrationPrivateAuthority({
    required this.controller,
    required this.runtime,
  });

  final AtlasAppController controller;
  final AtlasVaultPrivateStateRuntime runtime;

  @override
  bool get isEncryptedPrivateStateActive => runtime.isActive;

  @override
  void hideLegacyPrivateState() {
    controller._hideLegacyPrivateStateForMigration();
  }

  @override
  Future<bool> activateEncryptedPrivateState(String vaultId) async {
    return await controller._activateMigratedAtlasVault(vaultId) ==
        AtlasVaultActivationResult.activated;
  }

  @override
  Future<AtlasVaultPlaintextPrivateState> readEncryptedPrivateState() async {
    final snapshot = await runtime.read();
    return AtlasVaultPlaintextPrivateState(
      savedSearches: snapshot.savedSearches,
      trackerRecords: snapshot.trackerRecords,
    );
  }
}

class AtlasSearchSkeleton extends StatelessWidget {
  const AtlasSearchSkeleton({required this.controller, super.key});

  final AtlasAppController controller;

  static const _quickFilters = [
    _QuickFilter('Closing soon', AtlasIcons.deadline),
    _QuickFilter('Remote', AtlasIcons.remote),
    _QuickFilter('Best fit', AtlasIcons.target),
  ];

  @override
  Widget build(BuildContext context) {
    return AnimatedBuilder(
      animation: controller,
      builder: (context, _) {
        return ListView.builder(
          padding: const EdgeInsets.fromLTRB(16, 8, 16, 24),
          itemCount: _searchListItemCount(controller),
          itemBuilder: (context, index) {
            if (index == 0) {
              return TextField(
                textInputAction: TextInputAction.search,
                decoration: const InputDecoration(
                  prefixIcon: Icon(AtlasIcons.search),
                  labelText: 'Title, keyword, skill, or organization',
                ),
                onChanged: (value) {
                  controller.updateQuery(value);
                },
                onSubmitted: (_) {
                  controller.refreshLocalSave();
                },
              );
            }
            if (index == 1) {
              return const SizedBox(height: 12);
            }
            if (index == 2) {
              return SizedBox(
                height: 34,
                child: ListView.separated(
                  scrollDirection: Axis.horizontal,
                  itemCount:
                      controller.filters.activeChips.length +
                      _quickFilters.length,
                  separatorBuilder: (_, _) => const SizedBox(width: 8),
                  itemBuilder: (context, index) {
                    final activeChips = controller.filters.activeChips;
                    if (index < activeChips.length) {
                      final chip = activeChips[index];
                      return AtlasFilterChip(
                        label: chip.title,
                        icon: AtlasIcons.check,
                        selected: true,
                        onDeleted: () {
                          controller.removeActiveFilter(chip.id);
                        },
                      );
                    }
                    final filter = _quickFilters[index - activeChips.length];
                    return AtlasFilterChip(
                      label: filter.label,
                      icon: filter.icon,
                      selected: controller.isQuickFilterActive(filter.label),
                      onTap: () {
                        controller.toggleQuickFilter(filter.label);
                      },
                    );
                  },
                ),
              );
            }
            if (index == 3) {
              return const SizedBox(height: 14);
            }
            if (index == 4) {
              return AtlasSearchStatusBar(controller: controller);
            }
            if (index == 5 &&
                controller.connectionMessage != null &&
                controller.connectionStatus != 'Connected') {
              return Padding(
                padding: const EdgeInsets.only(top: 12),
                child: AtlasStatusBanner(
                  message: controller.connectionMessage!,
                ),
              );
            }
            if (index == _resultsStartIndex(controller) - 1) {
              return const SizedBox(height: 10);
            }
            if (controller.results.isEmpty) {
              return const AtlasEmptySearchState();
            }
            final resultIndex = index - _resultsStartIndex(controller);
            return AtlasJobResultTile(
              controller.results[resultIndex],
              controller: controller,
            );
          },
        );
      },
    );
  }

  static int _resultsStartIndex(AtlasAppController controller) {
    return controller.connectionMessage != null &&
            controller.connectionStatus != 'Connected'
        ? 7
        : 6;
  }

  static int _searchListItemCount(AtlasAppController controller) {
    final resultCount = controller.results.isEmpty
        ? 1
        : controller.results.length;
    return _resultsStartIndex(controller) + resultCount;
  }
}

class AtlasJobResultTile extends StatelessWidget {
  const AtlasJobResultTile(this.job, {required this.controller, super.key});

  final JobSearchResult job;
  final AtlasAppController controller;

  @override
  Widget build(BuildContext context) {
    return DecoratedBox(
      decoration: BoxDecoration(
        color: Colors.white,
        border: const Border(
          bottom: BorderSide(color: AtlasPalette.border, width: 0.8),
        ),
      ),
      child: InkWell(
        onTap: () {
          Navigator.of(context).push(
            MaterialPageRoute<void>(
              builder: (_) =>
                  AtlasJobDetailScreen(job: job, controller: controller),
            ),
          );
        },
        child: Padding(
          padding: const EdgeInsets.symmetric(horizontal: 2, vertical: 9),
          child: Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              AtlasSourceBadge(job: job),
              const SizedBox(width: 10),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(
                      job.title,
                      maxLines: 2,
                      overflow: TextOverflow.ellipsis,
                      style: const TextStyle(
                        color: AtlasPalette.ink,
                        fontSize: 15,
                        height: 1.18,
                        fontWeight: FontWeight.w800,
                      ),
                    ),
                    const SizedBox(height: 3),
                    Text(
                      '${job.organizationDisplay} · ${job.dutyStation}',
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                      style: const TextStyle(
                        color: AtlasPalette.muted,
                        fontSize: 12,
                        height: 1.2,
                      ),
                    ),
                    const SizedBox(height: 6),
                    Row(
                      children: [
                        AtlasDeadlinePill(job: job),
                        const SizedBox(width: 7),
                        Expanded(
                          child: Text(
                            _compactMetadata(job),
                            maxLines: 1,
                            overflow: TextOverflow.ellipsis,
                            style: const TextStyle(
                              color: AtlasPalette.muted,
                              fontSize: 12,
                              fontWeight: FontWeight.w600,
                            ),
                          ),
                        ),
                      ],
                    ),
                  ],
                ),
              ),
              const SizedBox(width: 4),
              Padding(
                padding: const EdgeInsets.only(top: 17),
                child: AnimatedBuilder(
                  animation: controller,
                  builder: (context, _) {
                    return Icon(
                      controller.isJobSaved(job.jobKey)
                          ? AtlasIcons.bookmarkFilled
                          : AtlasIcons.chevron,
                      size: 18,
                      color: controller.isJobSaved(job.jobKey)
                          ? AtlasPalette.accent
                          : AtlasPalette.muted,
                    );
                  },
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }

  String _compactMetadata(JobSearchResult job) {
    final grade = job.gradeCode.isEmpty ? 'Grade unknown' : job.gradeCode;
    final contract = job.contractLabel.isEmpty
        ? 'Contract unknown'
        : job.contractLabel;
    final modality = job.workModality.isEmpty ? 'Unknown' : job.workModality;
    return '$grade · $contract · $modality';
  }
}

class AtlasSourceBadge extends StatelessWidget {
  const AtlasSourceBadge({required this.job, super.key});

  final JobSearchResult job;

  @override
  Widget build(BuildContext context) {
    return Container(
      width: 34,
      height: 34,
      alignment: Alignment.center,
      decoration: BoxDecoration(
        color: _sourceColor(job.sourceID),
        borderRadius: BorderRadius.circular(8),
      ),
      child: Text(
        job.sourceInitials,
        maxLines: 1,
        overflow: TextOverflow.clip,
        style: const TextStyle(
          color: Colors.white,
          fontSize: 11,
          fontWeight: FontWeight.w800,
        ),
      ),
    );
  }
}

class AtlasDeadlinePill extends StatelessWidget {
  const AtlasDeadlinePill({required this.job, super.key});

  final JobSearchResult job;

  @override
  Widget build(BuildContext context) {
    final urgency = job.deadlineUrgency();
    final color = switch (urgency) {
      DeadlineUrgency.critical ||
      DeadlineUrgency.passed => AtlasPalette.deadlineRed,
      DeadlineUrgency.soon => AtlasPalette.deadlineAmber,
      _ => AtlasPalette.muted,
    };
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 7, vertical: 3),
      decoration: BoxDecoration(
        color: color.withValues(alpha: 0.10),
        borderRadius: BorderRadius.circular(999),
      ),
      child: Text(
        job.deadlineText(),
        style: TextStyle(
          color: color,
          fontSize: 11,
          fontWeight: FontWeight.w800,
        ),
      ),
    );
  }
}

class AtlasMetadataPill extends StatelessWidget {
  const AtlasMetadataPill({
    required this.icon,
    required this.label,
    this.color = AtlasPalette.muted,
    super.key,
  });

  final IconData icon;
  final String label;
  final Color color;

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 5),
      decoration: BoxDecoration(
        color: color.withValues(alpha: 0.10),
        borderRadius: BorderRadius.circular(8),
      ),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          Icon(icon, size: 14, color: color),
          const SizedBox(width: 4),
          Text(
            label,
            style: TextStyle(
              color: color,
              fontSize: 12,
              fontWeight: FontWeight.w700,
            ),
          ),
        ],
      ),
    );
  }
}

class AtlasStatusBanner extends StatelessWidget {
  const AtlasStatusBanner({required this.message, super.key});

  final String message;

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.all(10),
      decoration: BoxDecoration(
        color: AtlasPalette.accent.withValues(alpha: 0.08),
        borderRadius: BorderRadius.circular(8),
        border: Border.all(color: AtlasPalette.accent.withValues(alpha: 0.18)),
      ),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          const Icon(AtlasIcons.info, color: AtlasPalette.accent, size: 18),
          const SizedBox(width: 8),
          Expanded(
            child: Text(
              message,
              style: const TextStyle(
                color: AtlasPalette.ink,
                fontSize: 13,
                height: 1.3,
              ),
            ),
          ),
        ],
      ),
    );
  }
}

class AtlasSearchStatusBar extends StatelessWidget {
  const AtlasSearchStatusBar({required this.controller, super.key});

  final AtlasAppController controller;

  @override
  Widget build(BuildContext context) {
    return Row(
      children: [
        SizedBox.square(
          dimension: 18,
          child: controller.isSearching || controller.isRefreshingLocalSave
              ? const CircularProgressIndicator(strokeWidth: 2)
              : Icon(
                  controller.connectionStatus == 'Connected'
                      ? CupertinoIcons.wifi
                      : CupertinoIcons.wifi_slash,
                  size: 18,
                  color: controller.connectionStatus == 'Connected'
                      ? AtlasPalette.success
                      : AtlasPalette.deadlineAmber,
                ),
        ),
        const SizedBox(width: 8),
        Expanded(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(
                controller.resultCountLabel,
                style: const TextStyle(
                  fontSize: 14,
                  fontWeight: FontWeight.w700,
                ),
              ),
              const SizedBox(height: 2),
              Text(
                controller.statusSubtitle,
                maxLines: 1,
                overflow: TextOverflow.ellipsis,
                style: const TextStyle(fontSize: 12, color: AtlasPalette.muted),
              ),
            ],
          ),
        ),
        MenuAnchor(
          builder: (context, menuController, child) {
            return TextButton.icon(
              onPressed: menuController.open,
              icon: const Icon(AtlasIcons.sort, size: 18),
              label: Text('Sort: ${controller.sortOrder.label}'),
            );
          },
          menuChildren: [
            for (final order in SortOrder.values)
              MenuItemButton(
                onPressed: () {
                  controller.setSortOrder(order);
                },
                leadingIcon: order == controller.sortOrder
                    ? const Icon(AtlasIcons.check, size: 18)
                    : null,
                child: Text(order.label),
              ),
          ],
        ),
      ],
    );
  }
}

class AtlasEmptySearchState extends StatelessWidget {
  const AtlasEmptySearchState({super.key});

  @override
  Widget build(BuildContext context) {
    return ConstrainedBox(
      constraints: const BoxConstraints(minHeight: 260),
      child: const Center(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Icon(AtlasIcons.search, size: 48, color: AtlasPalette.accent),
            SizedBox(height: 14),
            Text(
              'No local save available',
              textAlign: TextAlign.center,
              style: TextStyle(fontSize: 20, fontWeight: FontWeight.w700),
            ),
            SizedBox(height: 8),
            Padding(
              padding: EdgeInsets.symmetric(horizontal: 12),
              child: Text(
                'Connect to the local server once and refresh the local save to enable offline search.',
                textAlign: TextAlign.center,
                style: TextStyle(fontSize: 14, color: AtlasPalette.muted),
              ),
            ),
          ],
        ),
      ),
    );
  }
}

class AtlasSearchActionGroup extends StatelessWidget {
  const AtlasSearchActionGroup({
    required this.controller,
    required this.onShowFilters,
    super.key,
  });

  final AtlasAppController controller;
  final VoidCallback onShowFilters;

  @override
  Widget build(BuildContext context) {
    return AnimatedBuilder(
      animation: controller,
      builder: (context, _) {
        return Container(
          height: 40,
          decoration: BoxDecoration(
            color: Colors.white,
            borderRadius: BorderRadius.circular(999),
            border: Border.all(color: AtlasPalette.border),
          ),
          child: Row(
            mainAxisSize: MainAxisSize.min,
            children: [
              Tooltip(
                message: 'Filters',
                child: IconButton(
                  onPressed: onShowFilters,
                  icon: Icon(
                    controller.filters.activeChips.length > 1
                        ? AtlasIcons.filter
                        : AtlasIcons.filter,
                    color: controller.filters.activeChips.length > 1
                        ? AtlasPalette.accent
                        : AtlasPalette.ink,
                  ),
                  visualDensity: VisualDensity.compact,
                ),
              ),
              Container(width: 1, height: 20, color: AtlasPalette.border),
              Tooltip(
                message: 'Save search',
                child: IconButton(
                  onPressed: controller.isSavingSearch
                      ? null
                      : () => unawaited(controller.saveCurrentSearch()),
                  icon: Icon(
                    controller.isSavingSearch
                        ? AtlasIcons.deadline
                        : controller.savedSearches.isEmpty
                        ? AtlasIcons.bookmark
                        : AtlasIcons.bookmarkFilled,
                    color: controller.savedSearches.isEmpty
                        ? AtlasPalette.ink
                        : AtlasPalette.accent,
                  ),
                  visualDensity: VisualDensity.compact,
                ),
              ),
            ],
          ),
        );
      },
    );
  }
}

class AtlasFilterSheet extends StatefulWidget {
  const AtlasFilterSheet({required this.controller, super.key});

  final AtlasAppController controller;

  @override
  State<AtlasFilterSheet> createState() => _AtlasFilterSheetState();
}

class _AtlasFilterSheetState extends State<AtlasFilterSheet> {
  late AtlasSearchFilters _draftFilters;
  late TextEditingController _cityController;
  late TextEditingController _countryController;
  late TextEditingController _capabilityController;

  static const _workModeOptions = <String>[
    'onsite',
    'home_based',
    'online_remote',
    'hybrid',
    'multiple_locations',
  ];

  static const _contractFallbacks = <String>[
    'staff',
    'consultant_contractor',
    'un_volunteer',
    'volunteer',
    'internship',
    'roster_pipeline',
    'unknown',
    'fellowship_ypp_similar',
  ];

  @override
  void initState() {
    super.initState();
    _draftFilters = widget.controller.filters;
    _cityController = TextEditingController(text: _draftFilters.city);
    _countryController = TextEditingController(
      text: _draftFilters.countryISO3.toUpperCase(),
    );
    _capabilityController = TextEditingController(
      text: _draftFilters.capabilityQuery,
    );
  }

  @override
  void dispose() {
    _cityController.dispose();
    _countryController.dispose();
    _capabilityController.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return AnimatedBuilder(
      animation: widget.controller,
      builder: (context, _) {
        return SafeArea(
          child: FractionallySizedBox(
            heightFactor: 0.92,
            child: Material(
              color: _FilterPalette.background,
              borderRadius: const BorderRadius.vertical(
                top: Radius.circular(18),
              ),
              clipBehavior: Clip.antiAlias,
              child: Column(
                children: [
                  Padding(
                    padding: const EdgeInsets.fromLTRB(18, 10, 10, 8),
                    child: Column(
                      children: [
                        Container(
                          width: 42,
                          height: 4,
                          decoration: BoxDecoration(
                            color: _FilterPalette.border,
                            borderRadius: BorderRadius.circular(999),
                          ),
                        ),
                        const SizedBox(height: 12),
                        Row(
                          children: [
                            const Expanded(
                              child: Text(
                                'Filters',
                                style: TextStyle(
                                  color: Colors.white,
                                  fontSize: 20,
                                  fontWeight: FontWeight.w800,
                                ),
                              ),
                            ),
                            TextButton(
                              onPressed: _applyAndClose,
                              child: const Text('Done'),
                            ),
                          ],
                        ),
                      ],
                    ),
                  ),
                  Expanded(
                    child: ListView(
                      padding: const EdgeInsets.fromLTRB(18, 4, 18, 18),
                      children: [
                        _FilterGroup(
                          title: 'Status',
                          summary: _statusSummary,
                          child: Column(
                            children: [
                              _DarkSwitchRow(
                                title: 'Open only',
                                subtitle: 'Hide closed and history rows',
                                value: _draftFilters.openOnly,
                                onChanged: (value) {
                                  _setDraft(
                                    _draftFilters.copyWith(openOnly: value),
                                  );
                                },
                              ),
                              _DarkSwitchRow(
                                title: 'Closing soon',
                                value: _draftFilters.closingSoon,
                                onChanged: (value) {
                                  _setDraft(
                                    _draftFilters.copyWith(closingSoon: value),
                                  );
                                },
                              ),
                            ],
                          ),
                        ),
                        _FilterGroup(
                          title: 'Location',
                          summary: _locationSummary,
                          child: Column(
                            crossAxisAlignment: CrossAxisAlignment.start,
                            children: [
                              Row(
                                children: [
                                  Expanded(
                                    child: _DarkTextField(
                                      controller: _cityController,
                                      label: 'City',
                                      hint: 'Any city',
                                      icon: AtlasIcons.location,
                                      onChanged: (value) {
                                        _draftFilters = _draftFilters.copyWith(
                                          city: value,
                                        );
                                        setState(() {});
                                      },
                                    ),
                                  ),
                                  const SizedBox(width: 10),
                                  SizedBox(
                                    width: 118,
                                    child: _DarkTextField(
                                      controller: _countryController,
                                      label: 'Country',
                                      hint: 'ISO3',
                                      icon: AtlasIcons.country,
                                      textCapitalization:
                                          TextCapitalization.characters,
                                      onChanged: (value) {
                                        _draftFilters = _draftFilters.copyWith(
                                          countryISO3: value.toUpperCase(),
                                        );
                                        setState(() {});
                                      },
                                    ),
                                  ),
                                ],
                              ),
                              const SizedBox(height: 10),
                              _FilterOptionGrid(
                                options: _cityOptions,
                                selectedIDs: _draftFilters.selectedCities,
                                enabled: (id) =>
                                    widget.controller.isFilterOptionEnabled(
                                      key: 'cities',
                                      value: id,
                                      filters: _draftFilters,
                                    ),
                                onToggle: (id) {
                                  final next = _locationSelectionText(
                                    _toggled(_draftFilters.selectedCities, id),
                                  );
                                  _cityController.text = next;
                                  _setDraft(_draftFilters.copyWith(city: next));
                                },
                              ),
                              const SizedBox(height: 8),
                              _FilterOptionGrid(
                                options: _countryOptions,
                                selectedIDs:
                                    _draftFilters.selectedCountriesISO3,
                                enabled: (id) =>
                                    widget.controller.isFilterOptionEnabled(
                                      key: 'countries',
                                      value: id,
                                      filters: _draftFilters,
                                    ),
                                onToggle: (id) {
                                  final next = _locationSelectionText(
                                    _toggled(
                                      _draftFilters.selectedCountriesISO3,
                                      id,
                                    ),
                                  );
                                  _countryController.text = next;
                                  _setDraft(
                                    _draftFilters.copyWith(countryISO3: next),
                                  );
                                },
                              ),
                              const SizedBox(height: 6),
                              _DarkSwitchRow(
                                title: 'Include uncertain matches',
                                value: _draftFilters.includeLowConfidence,
                                onChanged: (value) {
                                  _setDraft(
                                    _draftFilters.copyWith(
                                      includeLowConfidence: value,
                                    ),
                                  );
                                },
                              ),
                            ],
                          ),
                        ),
                        _FilterGroup(
                          title: 'Scope',
                          summary: _draftFilters.scope.title,
                          child: _FilterOptionGrid(
                            options: AtlasScopeFilter.values
                                .map(
                                  (scope) => AtlasFacetOption(
                                    id: scope.name,
                                    title: scope.title,
                                    count: 0,
                                  ),
                                )
                                .toList(growable: false),
                            selectedIDs: <String>{_draftFilters.scope.name},
                            showCounts: false,
                            enabled: (_) => true,
                            onToggle: (id) {
                              final scope = AtlasScopeFilter.values.firstWhere(
                                (scope) => scope.name == id,
                              );
                              _setDraft(_draftFilters.copyWith(scope: scope));
                            },
                          ),
                        ),
                        _FilterFacetGroup(
                          title: 'Contract',
                          emptySummary: 'Any contract',
                          options: _contractOptions,
                          selectedIDs: {
                            ..._draftFilters.contractGroups,
                            ..._draftFilters.volunteerKinds,
                          },
                          enabled: (id) => _contractOptionEnabled(id),
                          onToggle: _toggleContract,
                        ),
                        if (_draftFilters.volunteerKinds.contains(
                          AtlasVolunteerKind.unVolunteer.value,
                        ))
                          _FilterFacetGroup(
                            title: 'UN Volunteer Category',
                            emptySummary: 'Any UNV category',
                            options: _unvCategoryOptions,
                            selectedIDs: _draftFilters.unvCategories,
                            enabled: (id) =>
                                widget.controller.isFilterOptionEnabled(
                                  key: 'unv_categories',
                                  value: id,
                                  filters: _draftFilters,
                                ),
                            onToggle: (id) {
                              _setDraft(
                                _draftFilters.copyWith(
                                  unvCategories: _toggled(
                                    _draftFilters.unvCategories,
                                    id,
                                  ),
                                ),
                              );
                            },
                          ),
                        _FilterFacetGroup(
                          title: 'Seniority',
                          emptySummary: 'Any seniority',
                          options: _seniorityOptions,
                          selectedIDs: _draftFilters.seniorityGroups,
                          enabled: (id) =>
                              widget.controller.isFilterOptionEnabled(
                                key: 'seniority_groups',
                                value: id,
                                filters: _draftFilters,
                              ),
                          onToggle: (id) {
                            _setDraft(
                              _draftFilters.copyWith(
                                seniorityGroups: _toggled(
                                  _draftFilters.seniorityGroups,
                                  id,
                                ),
                              ),
                            );
                          },
                        ),
                        _FilterFacetGroup(
                          title: 'Grade',
                          emptySummary: 'Any grade',
                          options: _gradeOptions,
                          selectedIDs: _draftFilters.gradeCodes,
                          enabled: (id) =>
                              widget.controller.isFilterOptionEnabled(
                                key: 'grades',
                                value: id,
                                filters: _draftFilters,
                              ),
                          onToggle: (id) {
                            _setDraft(
                              _draftFilters.copyWith(
                                gradeCodes: _toggled(
                                  _draftFilters.gradeCodes,
                                  id,
                                ),
                              ),
                            );
                          },
                        ),
                        _FilterFacetGroup(
                          title: 'CCOG Family',
                          emptySummary: 'Any CCOG family',
                          options: widget.controller.availabilityFacetOptions(
                            'ccog_families',
                            filters: _draftFilters,
                            limit: 20,
                            selected: _draftFilters.ccogFamilies,
                          ),
                          selectedIDs: _draftFilters.ccogFamilies,
                          enabled: (id) =>
                              widget.controller.isFilterOptionEnabled(
                                key: 'ccog_families',
                                value: id,
                                filters: _draftFilters,
                              ),
                          onToggle: (id) {
                            _setDraft(
                              _draftFilters.copyWith(
                                ccogFamilies: _toggled(
                                  _draftFilters.ccogFamilies,
                                  id,
                                ),
                              ),
                            );
                          },
                        ),
                        _FilterFacetGroup(
                          title: 'Organizations',
                          emptySummary: 'Any organization',
                          options: widget.controller.availabilityFacetOptions(
                            'organizations',
                            filters: _draftFilters,
                            limit: 12,
                            selected: _draftFilters.organizations,
                          ),
                          selectedIDs: _draftFilters.organizations,
                          enabled: (id) =>
                              widget.controller.isFilterOptionEnabled(
                                key: 'organizations',
                                value: id,
                                filters: _draftFilters,
                              ),
                          onToggle: (id) {
                            _setDraft(
                              _draftFilters.copyWith(
                                organizations: _toggled(
                                  _draftFilters.organizations,
                                  id,
                                ),
                              ),
                            );
                          },
                        ),
                        _FilterFacetGroup(
                          title: 'Work Mode',
                          emptySummary: 'Any mode',
                          options: _workModeOptionsForDisplay,
                          selectedIDs: _draftFilters.workModalities,
                          enabled: (id) =>
                              widget.controller.isFilterOptionEnabled(
                                key: 'work_modalities',
                                value: id,
                                filters: _draftFilters,
                              ),
                          onToggle: (id) {
                            _setDraft(
                              _draftFilters.copyWith(
                                workModalities: _toggled(
                                  _draftFilters.workModalities,
                                  id,
                                ),
                              ),
                            );
                          },
                        ),
                        _FilterGroup(
                          title: 'Capability Tags',
                          summary: _draftFilters.trimmedCapabilityQuery.isEmpty
                              ? 'Any capability'
                              : _draftFilters.trimmedCapabilityQuery,
                          child: Column(
                            crossAxisAlignment: CrossAxisAlignment.start,
                            children: [
                              _DarkTextField(
                                controller: _capabilityController,
                                label: 'Keyword',
                                hint: 'data, programme, reporting',
                                icon: AtlasIcons.target,
                                onChanged: (value) {
                                  _draftFilters = _draftFilters.copyWith(
                                    capabilityQuery: value,
                                  );
                                  setState(() {});
                                },
                              ),
                              const SizedBox(height: 10),
                              _FilterOptionGrid(
                                options: widget.controller
                                    .availabilityFacetOptions(
                                      'capability_tags',
                                      filters: _draftFilters,
                                      limit: 18,
                                      selected: _draftFilters.capabilityTags,
                                    ),
                                selectedIDs: _draftFilters.capabilityTags,
                                enabled: (id) =>
                                    widget.controller.isFilterOptionEnabled(
                                      key: 'capability_tags',
                                      value: id,
                                      filters: _draftFilters,
                                    ),
                                onToggle: (id) {
                                  _setDraft(
                                    _draftFilters.copyWith(
                                      capabilityTags: _toggled(
                                        _draftFilters.capabilityTags,
                                        id,
                                      ),
                                    ),
                                  );
                                },
                              ),
                              const SizedBox(height: 6),
                              const _FilterHelpText(
                                'Type comma-separated keywords, or select capability tags. Multiple values in this group match any selected value.',
                              ),
                            ],
                          ),
                        ),
                      ],
                    ),
                  ),
                  Container(
                    padding: const EdgeInsets.fromLTRB(18, 12, 18, 18),
                    decoration: const BoxDecoration(
                      color: _FilterPalette.footer,
                      border: Border(
                        top: BorderSide(color: _FilterPalette.border),
                      ),
                    ),
                    child: Row(
                      children: [
                        Expanded(
                          child: OutlinedButton(
                            onPressed: _resetFilters,
                            style: OutlinedButton.styleFrom(
                              foregroundColor: Colors.white,
                              side: const BorderSide(
                                color: _FilterPalette.border,
                              ),
                            ),
                            child: const Text('Reset'),
                          ),
                        ),
                        const SizedBox(width: 12),
                        Expanded(
                          child: FilledButton(
                            onPressed: _applyAndClose,
                            style: FilledButton.styleFrom(
                              backgroundColor: AtlasPalette.accent,
                              foregroundColor: Colors.white,
                            ),
                            child: const Text('Apply filters'),
                          ),
                        ),
                      ],
                    ),
                  ),
                ],
              ),
            ),
          ),
        );
      },
    );
  }

  String get _statusSummary {
    if (_draftFilters.openOnly && _draftFilters.closingSoon) {
      return 'Open, closing soon';
    }
    if (_draftFilters.openOnly) {
      return 'Open only';
    }
    if (_draftFilters.closingSoon) {
      return 'Closing soon';
    }
    return 'All statuses';
  }

  String get _locationSummary {
    final cities = _draftFilters.selectedCities;
    final countries = _draftFilters.selectedCountriesISO3;
    final parts = <String>[
      if (cities.isNotEmpty) _compactSelectionSummary('City', cities),
      if (countries.isNotEmpty) _compactSelectionSummary('Country', countries),
    ];
    if (parts.isEmpty) {
      return 'Any location';
    }
    return parts.join(', ');
  }

  List<AtlasFacetOption> get _cityOptions {
    return widget.controller.availabilityFacetOptions(
      'cities',
      filters: _draftFilters,
      limit: 10,
      selected: _draftFilters.selectedCities,
    );
  }

  List<AtlasFacetOption> get _countryOptions {
    return widget.controller.availabilityFacetOptions(
      'countries',
      filters: _draftFilters,
      limit: 10,
      selected: _draftFilters.selectedCountriesISO3,
    );
  }

  List<AtlasFacetOption> get _contractOptions {
    final options = widget.controller.availabilityFacetOptions(
      'contract_groups',
      filters: _draftFilters,
      selected: _draftFilters.contractGroups,
    );
    final byID = {for (final option in options) option.id: option};
    for (final id in _contractFallbacks) {
      byID.putIfAbsent(
        id,
        () => AtlasFacetOption(
          id: id,
          title: id == AtlasVolunteerKind.unVolunteer.value
              ? AtlasVolunteerKind.unVolunteer.title
              : id == AtlasVolunteerKind.volunteer.value
              ? AtlasVolunteerKind.volunteer.title
              : displayAtlasFilterValue(id),
          count: 0,
        ),
      );
    }
    final volunteerFacets = widget.controller.availabilityFacetOptions(
      'volunteer_kinds',
      filters: _draftFilters,
      limit: 2,
      selected: _draftFilters.volunteerKinds,
    );
    for (final option in volunteerFacets) {
      byID[option.id] = option;
    }
    final sorted = byID.values.toList()
      ..sort(
        (left, right) =>
            _contractSortKey(left.id).compareTo(_contractSortKey(right.id)),
      );
    return sorted;
  }

  List<AtlasFacetOption> get _seniorityOptions {
    final options = widget.controller.availabilityFacetOptions(
      'seniority_groups',
      filters: _draftFilters,
      limit: 20,
      selected: _draftFilters.seniorityGroups,
    );
    final byID = {for (final option in options) option.id: option};
    final ordered = <AtlasFacetOption>[];
    for (final id in atlasSeniorityOrder) {
      final option = byID.remove(id);
      if (option != null || _draftFilters.seniorityGroups.contains(id)) {
        ordered.add(
          AtlasFacetOption(
            id: id,
            title: atlasSeniorityLabels[id] ?? displayAtlasFilterValue(id),
            count: option?.count ?? 0,
          ),
        );
      }
    }
    ordered.addAll(
      byID.values.map(
        (option) => AtlasFacetOption(
          id: option.id,
          title: atlasSeniorityLabels[option.id] ?? option.title,
          count: option.count,
        ),
      ),
    );
    return ordered;
  }

  List<AtlasFacetOption> get _gradeOptions {
    final options =
        widget.controller
            .availabilityFacetOptions(
              'grades',
              filters: _draftFilters,
              limit: 40,
              selected: _draftFilters.gradeCodes,
            )
            .toList()
          ..sort(
            (left, right) => _gradeOptionSortKey(
              left.id,
            ).compareTo(_gradeOptionSortKey(right.id)),
          );
    return options
        .map(
          (option) => AtlasFacetOption(
            id: option.id,
            title: _displayGradeOption(option.id),
            count: option.count,
          ),
        )
        .toList(growable: false);
  }

  List<AtlasFacetOption> get _unvCategoryOptions {
    final facets = widget.controller.availabilityFacetOptions(
      'unv_categories',
      filters: _draftFilters,
      limit: 12,
      selected: _draftFilters.unvCategories,
    );
    final counts = {for (final option in facets) option.id: option.count};
    return atlasUNVCategoryInfo
        .map(
          (category) => AtlasFacetOption(
            id: category.id,
            title: category.title,
            count: counts[category.id] ?? 0,
          ),
        )
        .toList(growable: false);
  }

  List<AtlasFacetOption> get _workModeOptionsForDisplay {
    final available = {
      for (final option in widget.controller.availabilityFacetOptions(
        'work_modalities',
        filters: _draftFilters,
        limit: 20,
        selected: _draftFilters.workModalities,
      ))
        option.id: option,
    };
    return _workModeOptions
        .map(
          (mode) =>
              available[mode] ??
              AtlasFacetOption(
                id: mode,
                title: displayAtlasFilterValue(mode),
                count: 0,
              ),
        )
        .toList(growable: false);
  }

  void _setDraft(AtlasSearchFilters filters) {
    setState(() {
      _draftFilters = filters;
      if (_cityController.text != _draftFilters.city) {
        _cityController.text = _draftFilters.city;
      }
      final country = _draftFilters.countryISO3.toUpperCase();
      if (_countryController.text != country) {
        _countryController.text = country;
      }
      if (_capabilityController.text != _draftFilters.capabilityQuery) {
        _capabilityController.text = _draftFilters.capabilityQuery;
      }
    });
  }

  bool _contractOptionEnabled(String id) {
    final key =
        id == AtlasVolunteerKind.unVolunteer.value ||
            id == AtlasVolunteerKind.volunteer.value
        ? 'volunteer_kinds'
        : 'contract_groups';
    return widget.controller.isFilterOptionEnabled(
      key: key,
      value: id,
      filters: _draftFilters,
    );
  }

  void _toggleContract(String id) {
    if (id == AtlasVolunteerKind.unVolunteer.value ||
        id == AtlasVolunteerKind.volunteer.value) {
      final nextVolunteerKinds = _toggled(_draftFilters.volunteerKinds, id);
      var nextSeniority = _draftFilters.seniorityGroups;
      var nextUNVCategories = _draftFilters.unvCategories;
      var nextUNVTypes = _draftFilters.unvVolunteerTypes;
      if (nextVolunteerKinds.contains(AtlasVolunteerKind.unVolunteer.value)) {
        nextSeniority = {...nextSeniority, 'volunteer'};
      } else {
        nextSeniority = {...nextSeniority}..remove('volunteer');
        nextUNVCategories = const <String>{};
        nextUNVTypes = const <String>{};
      }
      if (nextVolunteerKinds.contains(AtlasVolunteerKind.volunteer.value)) {
        nextSeniority = {...nextSeniority, 'generic_volunteer'};
      } else {
        nextSeniority = {...nextSeniority}..remove('generic_volunteer');
      }
      _setDraft(
        _draftFilters.copyWith(
          volunteerKinds: nextVolunteerKinds,
          seniorityGroups: nextSeniority,
          unvCategories: nextUNVCategories,
          unvVolunteerTypes: nextUNVTypes,
        ),
      );
      return;
    }
    _setDraft(
      _draftFilters.copyWith(
        contractGroups: _toggled(_draftFilters.contractGroups, id),
      ),
    );
  }

  Future<void> _resetFilters() async {
    _setDraft(AtlasSearchFilters());
    await widget.controller.resetFilters();
  }

  Future<void> _applyAndClose() async {
    await widget.controller.applyFilters(_draftFilters);
    if (mounted) {
      Navigator.of(context).pop();
    }
  }
}

class _FilterGroup extends StatelessWidget {
  const _FilterGroup({
    required this.title,
    required this.summary,
    required this.child,
  });

  final String title;
  final String summary;
  final Widget child;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.only(bottom: 18),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Expanded(
                child: Text(
                  title,
                  style: const TextStyle(
                    color: Colors.white,
                    fontSize: 15,
                    fontWeight: FontWeight.w800,
                  ),
                ),
              ),
              const SizedBox(width: 12),
              Flexible(
                child: Text(
                  summary,
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                  textAlign: TextAlign.right,
                  style: const TextStyle(
                    color: _FilterPalette.muted,
                    fontSize: 12,
                    fontWeight: FontWeight.w600,
                  ),
                ),
              ),
            ],
          ),
          const SizedBox(height: 10),
          child,
        ],
      ),
    );
  }
}

class _FilterFacetGroup extends StatelessWidget {
  const _FilterFacetGroup({
    required this.title,
    required this.emptySummary,
    required this.options,
    required this.selectedIDs,
    required this.enabled,
    required this.onToggle,
  });

  final String title;
  final String emptySummary;
  final List<AtlasFacetOption> options;
  final Set<String> selectedIDs;
  final bool Function(String id) enabled;
  final ValueChanged<String> onToggle;

  @override
  Widget build(BuildContext context) {
    final selectedOptions = options
        .where((option) => selectedIDs.contains(option.id))
        .toList(growable: false);
    final summary = selectedOptions.isEmpty
        ? emptySummary
        : selectedOptions.length == 1
        ? selectedOptions.single.title
        : '${selectedOptions.first.title} +${selectedOptions.length - 1}';
    return _FilterGroup(
      title: title,
      summary: summary,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          if (options.isEmpty)
            const _FilterHelpText(
              'No compatible values with the current filters',
            )
          else
            _FilterOptionGrid(
              options: options,
              selectedIDs: selectedIDs,
              enabled: enabled,
              onToggle: onToggle,
            ),
          const SizedBox(height: 6),
          const _FilterHelpText(
            'Dimmed values would return no jobs with the other active filters. Values in this group match any selected value.',
          ),
        ],
      ),
    );
  }
}

class _FilterOptionGrid extends StatelessWidget {
  const _FilterOptionGrid({
    required this.options,
    required this.selectedIDs,
    required this.enabled,
    required this.onToggle,
    this.showCounts = true,
  });

  final List<AtlasFacetOption> options;
  final Set<String> selectedIDs;
  final bool Function(String id) enabled;
  final ValueChanged<String> onToggle;
  final bool showCounts;

  @override
  Widget build(BuildContext context) {
    if (options.isEmpty) {
      return const SizedBox.shrink();
    }
    return LayoutBuilder(
      builder: (context, constraints) {
        final tileWidth = (constraints.maxWidth - 8) / 2;
        return Wrap(
          spacing: 8,
          runSpacing: 8,
          children: [
            for (final option in options)
              SizedBox(
                width: tileWidth.clamp(128, constraints.maxWidth).toDouble(),
                child: _FilterChoicePill(
                  option: option,
                  selected: selectedIDs.contains(option.id),
                  enabled:
                      enabled(option.id) || selectedIDs.contains(option.id),
                  showCount: showCounts,
                  onTap: () {
                    onToggle(option.id);
                  },
                ),
              ),
          ],
        );
      },
    );
  }
}

class _FilterChoicePill extends StatelessWidget {
  const _FilterChoicePill({
    required this.option,
    required this.selected,
    required this.enabled,
    required this.showCount,
    required this.onTap,
  });

  final AtlasFacetOption option;
  final bool selected;
  final bool enabled;
  final bool showCount;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final foreground = selected ? Colors.white : _FilterPalette.text;
    final border = selected ? AtlasPalette.accent : _FilterPalette.border;
    return Opacity(
      opacity: enabled ? 1 : 0.38,
      child: Material(
        color: Colors.transparent,
        child: InkWell(
          onTap: onTap,
          borderRadius: BorderRadius.circular(999),
          child: Container(
            height: 34,
            padding: const EdgeInsets.symmetric(horizontal: 9),
            decoration: BoxDecoration(
              color: selected ? AtlasPalette.accent : _FilterPalette.pill,
              borderRadius: BorderRadius.circular(999),
              border: Border.all(color: border),
            ),
            child: Row(
              children: [
                Icon(
                  selected ? AtlasIcons.check : AtlasIcons.circle,
                  color: foreground,
                  size: 15,
                ),
                const SizedBox(width: 6),
                Expanded(
                  child: Text(
                    option.title,
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                    style: TextStyle(
                      color: foreground,
                      fontSize: 12,
                      fontWeight: selected ? FontWeight.w800 : FontWeight.w600,
                    ),
                  ),
                ),
                if (showCount) ...[
                  const SizedBox(width: 6),
                  Text(
                    _formatCount(option.count),
                    style: TextStyle(
                      color: selected ? Colors.white70 : _FilterPalette.muted,
                      fontSize: 11,
                      fontFeatures: const [FontFeature.tabularFigures()],
                    ),
                  ),
                ],
              ],
            ),
          ),
        ),
      ),
    );
  }
}

class _DarkSwitchRow extends StatelessWidget {
  const _DarkSwitchRow({
    required this.title,
    required this.value,
    required this.onChanged,
    this.subtitle,
  });

  final String title;
  final String? subtitle;
  final bool value;
  final ValueChanged<bool> onChanged;

  @override
  Widget build(BuildContext context) {
    return SwitchListTile(
      contentPadding: EdgeInsets.zero,
      dense: true,
      title: Text(
        title,
        style: const TextStyle(
          color: _FilterPalette.text,
          fontSize: 13,
          fontWeight: FontWeight.w700,
        ),
      ),
      subtitle: subtitle == null
          ? null
          : Text(
              subtitle!,
              style: const TextStyle(color: _FilterPalette.muted, fontSize: 11),
            ),
      value: value,
      activeThumbColor: Colors.white,
      activeTrackColor: AtlasPalette.accent,
      inactiveThumbColor: _FilterPalette.muted,
      inactiveTrackColor: _FilterPalette.border,
      onChanged: onChanged,
    );
  }
}

class _DarkTextField extends StatelessWidget {
  const _DarkTextField({
    required this.controller,
    required this.label,
    required this.hint,
    required this.icon,
    required this.onChanged,
    this.textCapitalization = TextCapitalization.none,
  });

  final TextEditingController controller;
  final String label;
  final String hint;
  final IconData icon;
  final ValueChanged<String> onChanged;
  final TextCapitalization textCapitalization;

  @override
  Widget build(BuildContext context) {
    return TextField(
      controller: controller,
      textCapitalization: textCapitalization,
      onChanged: onChanged,
      style: const TextStyle(color: Colors.white, fontSize: 13),
      decoration: InputDecoration(
        isDense: true,
        labelText: label,
        hintText: hint,
        prefixIcon: Icon(icon, size: 18),
        labelStyle: const TextStyle(color: _FilterPalette.muted),
        hintStyle: const TextStyle(color: _FilterPalette.muted),
        prefixIconColor: _FilterPalette.muted,
        filled: true,
        fillColor: _FilterPalette.pill,
        enabledBorder: OutlineInputBorder(
          borderRadius: BorderRadius.circular(10),
          borderSide: const BorderSide(color: _FilterPalette.border),
        ),
        focusedBorder: OutlineInputBorder(
          borderRadius: BorderRadius.circular(10),
          borderSide: const BorderSide(color: AtlasPalette.accent),
        ),
      ),
    );
  }
}

class _FilterHelpText extends StatelessWidget {
  const _FilterHelpText(this.text);

  final String text;

  @override
  Widget build(BuildContext context) {
    return Text(
      text,
      style: const TextStyle(
        color: _FilterPalette.muted,
        fontSize: 11,
        height: 1.25,
      ),
    );
  }
}

Set<String> _toggled(Set<String> values, String id) {
  final next = values.toSet();
  if (!next.add(id)) {
    next.remove(id);
  }
  return next;
}

String _locationSelectionText(Set<String> values) {
  final sorted = values.toList()..sort();
  return sorted.join(', ');
}

String _compactSelectionSummary(String prefix, Set<String> values) {
  final sorted = values.toList()..sort();
  if (sorted.isEmpty) {
    return prefix;
  }
  final first = displayAtlasFilterValue(sorted.first);
  if (sorted.length == 1) {
    return '$prefix: $first';
  }
  return '$prefix: $first +${sorted.length - 1}';
}

String _contractSortKey(String value) {
  const order = <String>[
    'staff',
    'consultant_contractor',
    'un_volunteer',
    'volunteer',
    'internship',
    'roster_pipeline',
    'other',
    'unknown',
    'fellowship_ypp_similar',
  ];
  final index = order.indexOf(value);
  return '${(index < 0 ? order.length : index).toString().padLeft(3, '0')}-$value';
}

String _displayGradeOption(String value) {
  final compact = value.replaceAll('-', '').toUpperCase();
  final digitIndex = compact.indexOf(RegExp('[0-9]'));
  if (compact.length >= 2 && digitIndex > 0) {
    return '${compact.substring(0, digitIndex)}-${compact.substring(digitIndex)}';
  }
  return compact;
}

String _gradeOptionSortKey(String value) {
  final compact = value.replaceAll('-', '').toUpperCase();
  final digitIndex = compact.indexOf(RegExp('[0-9]'));
  if (digitIndex < 0) {
    return compact;
  }
  final letters = compact.substring(0, digitIndex);
  final numbers = compact.substring(digitIndex);
  final padded = (int.tryParse(numbers) ?? 0).toString().padLeft(3, '0');
  return '$letters$padded';
}

abstract final class _FilterPalette {
  static const background = Color(0xFF101820);
  static const footer = Color(0xFF0C131A);
  static const pill = Color(0xFF17222C);
  static const border = Color(0xFF344450);
  static const text = Color(0xFFEAF1F7);
  static const muted = Color(0xFF9EAAB5);
}

class AtlasUpdatesPanel extends StatelessWidget {
  const AtlasUpdatesPanel({required this.controller, super.key});

  final AtlasAppController controller;

  @override
  Widget build(BuildContext context) {
    return AnimatedBuilder(
      animation: controller,
      builder: (context, _) {
        final health = controller.healthSummary;
        final hidden = controller.hiddenDeadlinePastOpenJobs;
        return ListView(
          padding: const EdgeInsets.fromLTRB(16, 16, 16, 24),
          children: [
            const AtlasPanelHeader(
              title: 'Source Updates',
              icon: AtlasIcons.updates,
              subtitle: 'Refresh status, local save state, and count health.',
            ),
            const SizedBox(height: 14),
            Wrap(
              spacing: 8,
              runSpacing: 8,
              children: [
                AtlasStatTile(
                  label: 'Search',
                  value: _formatCount(controller.total),
                  supporting: controller.filters.openOnly
                      ? 'searchable open rows'
                      : 'filtered rows',
                ),
                AtlasStatTile(
                  label: 'Health',
                  value: health?.openJobs == null
                      ? 'Unknown'
                      : _formatCount(health!.openJobs!),
                  supporting: 'raw open rows',
                ),
                AtlasStatTile(
                  label: 'Sources',
                  value: health?.enabledSources == null
                      ? _formatCount(controller.sources.length)
                      : _formatCount(health!.enabledSources!),
                  supporting: 'enabled sources',
                ),
              ],
            ),
            if (hidden != null) ...[
              const SizedBox(height: 10),
              AtlasInfoStrip(
                icon: AtlasIcons.info,
                title: 'Count reconciliation',
                body:
                    '${_formatCount(hidden)} rows are still marked open in health, but have passed deadlines and are hidden from Search.',
              ),
            ],
            const SizedBox(height: 10),
            AtlasInfoStrip(
              icon: AtlasIcons.saveOffline,
              title: 'Local save',
              body: controller.cacheSavedAt == null
                  ? 'No local save refreshed in this app session.'
                  : '${_formatCount(controller.cachedJobCount)} rows cached · updated ${_formatSavedAt(controller.cacheSavedAt!)}.',
            ),
            if (health?.lastSyncAt != null) ...[
              const SizedBox(height: 10),
              AtlasInfoStrip(
                icon: AtlasIcons.refresh,
                title: 'Backend snapshot',
                body:
                    'Last sync ${_compactTimestamp(health!.lastSyncAt!)} from ${_formatBaseURL(controller.baseURL)}.',
              ),
            ],
            const SizedBox(height: 18),
            const Text(
              'Recent Runs',
              style: TextStyle(
                color: AtlasPalette.ink,
                fontSize: 16,
                fontWeight: FontWeight.w800,
              ),
            ),
            const SizedBox(height: 6),
            if (controller.updateRuns.isEmpty)
              const AtlasDataEmptyState(
                icon: AtlasIcons.updates,
                title: 'No refresh runs available',
                body:
                    'The server did not return recent source-run data for this session.',
              )
            else
              for (final run in controller.updateRuns.take(24))
                AtlasUpdateRunTile(run: run),
          ],
        );
      },
    );
  }
}

class AtlasSourcesPanel extends StatelessWidget {
  const AtlasSourcesPanel({
    required this.controller,
    required this.onSourceSelected,
    super.key,
  });

  final AtlasAppController controller;
  final ValueChanged<AtlasSourceSummary> onSourceSelected;

  @override
  Widget build(BuildContext context) {
    return AnimatedBuilder(
      animation: controller,
      builder: (context, _) {
        final sourceCount = controller.sources.length;
        final totalOpen = controller.sources.fold<int>(
          0,
          (sum, source) => sum + source.openJobs,
        );
        final degraded = controller.sources.where(_sourceNeedsAttention).length;
        return ListView(
          padding: const EdgeInsets.fromLTRB(16, 16, 16, 24),
          children: [
            const AtlasPanelHeader(
              title: 'Source Health',
              icon: AtlasIcons.sources,
              subtitle: 'Organizations, source status, and open job coverage.',
            ),
            const SizedBox(height: 14),
            Wrap(
              spacing: 8,
              runSpacing: 8,
              children: [
                AtlasStatTile(
                  label: 'Sources',
                  value: _formatCount(sourceCount),
                  supporting: 'returned by API',
                ),
                AtlasStatTile(
                  label: 'Open',
                  value: _formatCount(totalOpen),
                  supporting: 'across listed sources',
                ),
                AtlasStatTile(
                  label: 'Warnings',
                  value: _formatCount(degraded),
                  supporting: 'need attention',
                ),
              ],
            ),
            const SizedBox(height: 12),
            if (controller.sources.isEmpty)
              const AtlasDataEmptyState(
                icon: AtlasIcons.search,
                title: 'No source health returned',
                body:
                    'Connect and refresh local save to load source status from the API.',
              )
            else
              for (final source in controller.sources.take(80))
                AtlasSourceHealthTile(
                  source: source,
                  onTap: () => onSourceSelected(source),
                ),
          ],
        );
      },
    );
  }
}

class AtlasPanelHeader extends StatelessWidget {
  const AtlasPanelHeader({
    required this.title,
    required this.icon,
    required this.subtitle,
    super.key,
  });

  final String title;
  final IconData icon;
  final String subtitle;

  @override
  Widget build(BuildContext context) {
    return Row(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Container(
          width: 40,
          height: 40,
          decoration: BoxDecoration(
            color: AtlasPalette.accent.withValues(alpha: 0.12),
            borderRadius: BorderRadius.circular(8),
          ),
          child: Icon(icon, color: AtlasPalette.accent),
        ),
        const SizedBox(width: 12),
        Expanded(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(
                title,
                style: const TextStyle(
                  fontSize: 22,
                  fontWeight: FontWeight.w800,
                  color: AtlasPalette.ink,
                ),
              ),
              const SizedBox(height: 4),
              Text(
                subtitle,
                style: const TextStyle(
                  fontSize: 13,
                  height: 1.3,
                  color: AtlasPalette.muted,
                ),
              ),
            ],
          ),
        ),
      ],
    );
  }
}

class AtlasStatTile extends StatelessWidget {
  const AtlasStatTile({
    required this.label,
    required this.value,
    required this.supporting,
    super.key,
  });

  final String label;
  final String value;
  final String supporting;

  @override
  Widget build(BuildContext context) {
    return Container(
      width: 112,
      padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 9),
      decoration: BoxDecoration(
        color: Colors.white,
        borderRadius: BorderRadius.circular(8),
        border: Border.all(color: AtlasPalette.border),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(
            label,
            maxLines: 1,
            overflow: TextOverflow.ellipsis,
            style: const TextStyle(
              color: AtlasPalette.muted,
              fontSize: 11,
              fontWeight: FontWeight.w700,
            ),
          ),
          const SizedBox(height: 4),
          Text(
            value,
            maxLines: 1,
            overflow: TextOverflow.ellipsis,
            style: const TextStyle(
              color: AtlasPalette.ink,
              fontSize: 18,
              fontWeight: FontWeight.w800,
            ),
          ),
          const SizedBox(height: 2),
          Text(
            supporting,
            maxLines: 2,
            overflow: TextOverflow.ellipsis,
            style: const TextStyle(
              color: AtlasPalette.muted,
              fontSize: 11,
              height: 1.15,
            ),
          ),
        ],
      ),
    );
  }
}

class AtlasInfoStrip extends StatelessWidget {
  const AtlasInfoStrip({
    required this.icon,
    required this.title,
    required this.body,
    super.key,
  });

  final IconData icon;
  final String title;
  final String body;

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.all(10),
      decoration: BoxDecoration(
        color: Colors.white,
        borderRadius: BorderRadius.circular(8),
        border: Border.all(color: AtlasPalette.border),
      ),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Icon(icon, color: AtlasPalette.accent, size: 18),
          const SizedBox(width: 9),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  title,
                  style: const TextStyle(
                    color: AtlasPalette.ink,
                    fontSize: 13,
                    fontWeight: FontWeight.w800,
                  ),
                ),
                const SizedBox(height: 2),
                Text(
                  body,
                  style: const TextStyle(
                    color: AtlasPalette.muted,
                    fontSize: 12,
                    height: 1.3,
                  ),
                ),
              ],
            ),
          ),
        ],
      ),
    );
  }
}

class AtlasDataEmptyState extends StatelessWidget {
  const AtlasDataEmptyState({
    required this.icon,
    required this.title,
    required this.body,
    super.key,
  });

  final IconData icon;
  final String title;
  final String body;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 22),
      child: Column(
        children: [
          Icon(icon, size: 38, color: AtlasPalette.muted),
          const SizedBox(height: 10),
          Text(
            title,
            textAlign: TextAlign.center,
            style: const TextStyle(
              color: AtlasPalette.ink,
              fontSize: 16,
              fontWeight: FontWeight.w800,
            ),
          ),
          const SizedBox(height: 5),
          Text(
            body,
            textAlign: TextAlign.center,
            style: const TextStyle(
              color: AtlasPalette.muted,
              fontSize: 13,
              height: 1.3,
            ),
          ),
        ],
      ),
    );
  }
}

class AtlasUpdateRunTile extends StatelessWidget {
  const AtlasUpdateRunTile({required this.run, super.key});

  final AtlasSourceRun run;

  @override
  Widget build(BuildContext context) {
    return ListTile(
      contentPadding: EdgeInsets.zero,
      dense: true,
      leading: const Icon(
        AtlasIcons.refresh,
        color: AtlasPalette.accent,
        size: 22,
      ),
      title: Text(
        _humanSourceName(run.sourceID),
        maxLines: 1,
        overflow: TextOverflow.ellipsis,
        style: const TextStyle(fontWeight: FontWeight.w800),
      ),
      subtitle: Text(
        'Fetched ${_formatCount(run.fetched)} · new ${_formatCount(run.inserted)} · updated ${_formatCount(run.updated)} · closed ${_formatCount(run.closed)} · missing ${_formatCount(run.missing)}',
        maxLines: 2,
        overflow: TextOverflow.ellipsis,
      ),
      trailing: Text(
        run.observedAt == null ? 'No time' : _compactTimestamp(run.observedAt!),
        textAlign: TextAlign.end,
        style: const TextStyle(color: AtlasPalette.muted, fontSize: 11),
      ),
    );
  }
}

class AtlasSourceHealthTile extends StatelessWidget {
  const AtlasSourceHealthTile({
    required this.source,
    required this.onTap,
    super.key,
  });

  final AtlasSourceSummary source;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final warning = _sourceNeedsAttention(source);
    return ListTile(
      contentPadding: EdgeInsets.zero,
      dense: true,
      onTap: onTap,
      leading: Icon(
        warning ? AtlasIcons.warning : AtlasIcons.check,
        color: warning ? AtlasPalette.deadlineAmber : AtlasPalette.success,
        size: 22,
      ),
      title: Text(
        _humanSourceName(
          source.organization.isEmpty ? source.sourceID : source.organization,
        ),
        maxLines: 1,
        overflow: TextOverflow.ellipsis,
        style: const TextStyle(fontWeight: FontWeight.w800),
      ),
      subtitle: Text(
        '${source.sourceID} · ${_formatCount(source.openJobs)} open · ${_formatCount(source.totalJobs)} total',
        maxLines: 1,
        overflow: TextOverflow.ellipsis,
      ),
      trailing: Column(
        mainAxisAlignment: MainAxisAlignment.center,
        crossAxisAlignment: CrossAxisAlignment.end,
        children: [
          Text(
            source.healthStatus ?? 'unknown',
            style: TextStyle(
              color: warning ? AtlasPalette.deadlineAmber : AtlasPalette.muted,
              fontSize: 12,
              fontWeight: FontWeight.w800,
            ),
          ),
          const SizedBox(height: 2),
          Text(
            source.lastSeenAt == null
                ? 'No seen time'
                : _compactTimestamp(source.lastSeenAt!),
            style: const TextStyle(color: AtlasPalette.muted, fontSize: 11),
          ),
        ],
      ),
    );
  }
}

class AtlasSavedPanel extends StatelessWidget {
  const AtlasSavedPanel({required this.controller, super.key});

  final AtlasAppController controller;

  @override
  Widget build(BuildContext context) {
    return AnimatedBuilder(
      animation: controller,
      builder: (context, _) {
        if (controller.savedSearches.isEmpty &&
            controller.trackerRecords.isEmpty) {
          return const AtlasPlaceholderPanel(
            title: 'Saved Searches',
            icon: AtlasIcons.bookmark,
            summary:
                'Saved searches and tracked applications will appear here.',
          );
        }
        return ListView(
          padding: const EdgeInsets.fromLTRB(16, 16, 16, 24),
          children: [
            const Text(
              'Saved',
              style: TextStyle(
                color: AtlasPalette.ink,
                fontSize: 22,
                fontWeight: FontWeight.w800,
              ),
            ),
            const SizedBox(height: 10),
            if (controller.trackerRecords.isNotEmpty) ...[
              const Text(
                'Saved Jobs',
                style: TextStyle(
                  color: AtlasPalette.ink,
                  fontSize: 16,
                  fontWeight: FontWeight.w800,
                ),
              ),
              const SizedBox(height: 4),
              for (final record in controller.trackerRecords)
                AtlasSavedJobTile(record: record, controller: controller),
              if (controller.savedSearches.isNotEmpty)
                const SizedBox(height: 14),
            ],
            if (controller.savedSearches.isNotEmpty) ...[
              const Text(
                'Saved Searches',
                style: TextStyle(
                  color: AtlasPalette.ink,
                  fontSize: 16,
                  fontWeight: FontWeight.w800,
                ),
              ),
              const SizedBox(height: 4),
            ],
            for (final search in controller.savedSearches)
              ListTile(
                contentPadding: EdgeInsets.zero,
                leading: const Icon(
                  AtlasIcons.bookmarkFilled,
                  color: AtlasPalette.accent,
                ),
                title: Text(search.name),
                subtitle: Text(search.description ?? 'Saved search'),
                trailing: const Icon(AtlasIcons.chevron),
                onTap: () {
                  controller.runSavedSearch(search);
                },
              ),
          ],
        );
      },
    );
  }
}

class AtlasSavedJobTile extends StatelessWidget {
  const AtlasSavedJobTile({
    required this.record,
    required this.controller,
    super.key,
  });

  final AtlasApplicationRecord record;
  final AtlasAppController controller;

  @override
  Widget build(BuildContext context) {
    final job = _jobFromSavedRecord(record);
    return ListTile(
      contentPadding: EdgeInsets.zero,
      leading: const Icon(
        AtlasIcons.bookmarkFilled,
        color: AtlasPalette.accent,
      ),
      title: Text(job.title, maxLines: 1, overflow: TextOverflow.ellipsis),
      subtitle: Text(
        '${job.organizationDisplay} · ${_humanSourceName(record.status)}',
        maxLines: 1,
        overflow: TextOverflow.ellipsis,
      ),
      trailing: const Icon(AtlasIcons.chevron),
      onTap: () {
        Navigator.of(context).push(
          MaterialPageRoute<void>(
            builder: (_) =>
                AtlasJobDetailScreen(job: job, controller: controller),
          ),
        );
      },
    );
  }
}

JobSearchResult _jobFromSavedRecord(AtlasApplicationRecord record) {
  final parts = record.jobKey.split(':');
  final sourceID = parts.isEmpty ? 'unknown' : parts.first;
  final externalID = parts.length > 1 ? parts.sublist(1).join(':') : record.id;
  return JobSearchResult(
    jobKey: record.jobKey,
    title: externalID.isEmpty ? 'Saved vacancy' : 'Saved vacancy $externalID',
    organization: _humanSourceName(sourceID),
    sourceID: sourceID,
    dutyStation: 'Location unknown',
    gradeCode: '',
    contractLabel: 'Saved job',
    workModality: 'Unknown',
    closingDate: null,
    needsReview: true,
    scoreReasons: const <String>[],
    matchSummary: 'Saved from the application tracker.',
    description: 'Open detail to load the latest saved vacancy record.',
    status: record.status,
  );
}

class AtlasJobDetailScreen extends StatefulWidget {
  const AtlasJobDetailScreen({
    required this.job,
    required this.controller,
    super.key,
  });

  final JobSearchResult job;
  final AtlasAppController controller;

  @override
  State<AtlasJobDetailScreen> createState() => _AtlasJobDetailScreenState();
}

class _AtlasJobDetailScreenState extends State<AtlasJobDetailScreen> {
  late final Future<AtlasJobDetail> _detailFuture;
  bool _isSaving = false;

  @override
  void initState() {
    super.initState();
    _detailFuture = widget.controller.loadJobDetail(widget.job.jobKey);
  }

  @override
  Widget build(BuildContext context) {
    return AnimatedBuilder(
      animation: widget.controller,
      builder: (context, _) {
        final saved = widget.controller.isJobSaved(widget.job.jobKey);
        return Scaffold(
          appBar: AppBar(
            title: const Text('Job Detail'),
            actions: [
              Tooltip(
                message: saved ? 'Saved job' : 'Save job',
                child: IconButton(
                  onPressed: _isSaving || saved ? null : _saveJob,
                  icon: Icon(
                    saved ? AtlasIcons.bookmarkFilled : AtlasIcons.bookmark,
                    color: saved ? AtlasPalette.accent : AtlasPalette.ink,
                  ),
                ),
              ),
            ],
          ),
          body: FutureBuilder<AtlasJobDetail>(
            future: _detailFuture,
            builder: (context, snapshot) {
              return _AtlasJobDetailBody(
                job: widget.job,
                detail: snapshot.data,
                isLoading: snapshot.connectionState != ConnectionState.done,
                error: snapshot.hasError ? snapshot.error : null,
              );
            },
          ),
        );
      },
    );
  }

  Future<void> _saveJob() async {
    setState(() {
      _isSaving = true;
    });
    await widget.controller.saveJob(widget.job);
    if (!mounted) {
      return;
    }
    setState(() {
      _isSaving = false;
    });
  }
}

class _AtlasJobDetailBody extends StatelessWidget {
  const _AtlasJobDetailBody({
    required this.job,
    required this.detail,
    required this.isLoading,
    required this.error,
  });

  final JobSearchResult job;
  final AtlasJobDetail? detail;
  final bool isLoading;
  final Object? error;

  @override
  Widget build(BuildContext context) {
    final title = _firstNonEmpty([detail?.title, job.title]);
    final organizationLine = '${job.organizationDisplay} · ${job.dutyStation}';
    final fullDescription = _firstNonEmpty([
      detail?.description,
      _sectionBody(detail, 'Full Description'),
      job.description,
    ]);
    final contentSections = _contentSections(detail);
    final diagnosticSections = _diagnosticSections(detail);
    final formattedDetail = AtlasATSDetailFormatter.format(
      sections: contentSections,
    );
    final qualityStatus = _detailQualityStatus(detail);
    final weakDetail =
        job.needsReview ||
        (qualityStatus != null && qualityStatus.toLowerCase() != 'complete') ||
        (!isLoading && fullDescription.isEmpty && contentSections.isEmpty);
    final applyURL = detail?.applyURL ?? job.applyURL;
    final sourceURL = detail?.sourceURL ?? job.sourceURL;

    return ListView(
      padding: const EdgeInsets.fromLTRB(16, 16, 16, 24),
      children: [
        Text(
          title,
          style: const TextStyle(
            color: AtlasPalette.ink,
            fontSize: 22,
            height: 1.15,
            fontWeight: FontWeight.w800,
          ),
        ),
        const SizedBox(height: 8),
        Text(
          organizationLine,
          style: const TextStyle(color: AtlasPalette.muted, fontSize: 14),
        ),
        const SizedBox(height: 12),
        Wrap(
          spacing: 8,
          runSpacing: 8,
          children: [
            AtlasDeadlinePill(job: job),
            AtlasMetadataPill(
              icon: AtlasIcons.organization,
              label: job.gradeCode.isEmpty ? 'Grade unknown' : job.gradeCode,
            ),
            AtlasMetadataPill(
              icon: AtlasIcons.contract,
              label: job.contractLabel.isEmpty
                  ? 'Contract unknown'
                  : job.contractLabel,
            ),
            AtlasMetadataPill(
              icon: AtlasIcons.location,
              label: job.workModality.isEmpty ? 'Unknown' : job.workModality,
            ),
            AtlasMetadataPill(
              icon: AtlasIcons.country,
              label: _displayScope(job.nationalInternational),
            ),
            if (detail?.status != null || job.status.isNotEmpty)
              AtlasMetadataPill(
                icon: AtlasIcons.info,
                label: _firstNonEmpty([detail?.status, job.status]),
              ),
          ],
        ),
        if (isLoading) ...[
          const SizedBox(height: 16),
          const LinearProgressIndicator(minHeight: 2),
        ],
        if (error != null) ...[
          const SizedBox(height: 14),
          AtlasInfoStrip(
            icon: AtlasIcons.warning,
            title: 'Detail load failed',
            body: '$error',
          ),
        ],
        if (weakDetail) ...[
          const SizedBox(height: 14),
          const AtlasInfoStrip(
            icon: AtlasIcons.check,
            title: 'Weak detail state',
            body:
                'This posting has limited structured detail. Use the source or apply link for the authoritative vacancy text.',
          ),
        ],
        const SizedBox(height: 20),
        const _DetailSectionTitle('Full Description'),
        const SizedBox(height: 6),
        Text(
          fullDescription.isEmpty
              ? 'No full description was returned for this job.'
              : fullDescription,
          style: const TextStyle(
            color: AtlasPalette.ink,
            fontSize: 14,
            height: 1.38,
          ),
        ),
        const SizedBox(height: 20),
        const _DetailSectionTitle('Core Details'),
        const SizedBox(height: 6),
        _DetailRows(
          rows: [
            MapEntry('Deadline', job.deadlineText()),
            MapEntry(
              'Source deadline',
              _firstNonEmpty([
                detail?.deadlineInfo?.sourceText,
                detail?.closesAtLocal,
                detail?.closingDate,
              ]),
            ),
            MapEntry(
              'Grade',
              job.gradeCode.isEmpty ? 'Unknown' : job.gradeCode,
            ),
            MapEntry(
              'Contract',
              job.contractLabel.isEmpty ? 'Unknown' : job.contractLabel,
            ),
            MapEntry('Scope', _displayScope(job.nationalInternational)),
            MapEntry('Remote/onsite', job.workModality),
          ],
        ),
        if (formattedDetail.hiddenBoilerplate) ...[
          const SizedBox(height: 16),
          const AtlasInfoStrip(
            icon: AtlasIcons.info,
            title: 'ATS page chrome hidden',
            body:
                'Navigation, apply-button, and sharing text from the source page were removed from this display. Use the source link for the original posting.',
          ),
        ],
        if (formattedDetail.sections.isNotEmpty) ...[
          const SizedBox(height: 20),
          for (final section in formattedDetail.sections) ...[
            _FormattedDetailSectionView(section: section),
            const SizedBox(height: 16),
          ],
        ],
        if (formattedDetail.sections.isEmpty && !isLoading) ...[
          const SizedBox(height: 16),
          const AtlasInfoStrip(
            icon: AtlasIcons.info,
            title: 'Structured sections unavailable',
            body:
                'Responsibilities, qualifications, and other sections were not returned separately for this source.',
          ),
        ],
        const SizedBox(height: 4),
        const _DetailSectionTitle('Links'),
        const SizedBox(height: 6),
        if (applyURL == null && sourceURL == null)
          const Text(
            'No apply or source URL returned.',
            style: TextStyle(color: AtlasPalette.muted, fontSize: 13),
          )
        else ...[
          if (applyURL != null)
            _CopyLinkTile(label: 'Apply URL', url: applyURL.toString()),
          if (sourceURL != null)
            _CopyLinkTile(label: 'Source URL', url: sourceURL.toString()),
        ],
        const SizedBox(height: 12),
        ExpansionTile(
          tilePadding: EdgeInsets.zero,
          title: const Text(
            'Match diagnostics',
            style: TextStyle(fontWeight: FontWeight.w800),
          ),
          subtitle: const Text('Hidden from Search results by default'),
          childrenPadding: const EdgeInsets.only(bottom: 12),
          children: [
            Align(
              alignment: Alignment.centerLeft,
              child: Text(
                job.matchSummary,
                style: const TextStyle(
                  color: AtlasPalette.ink,
                  fontSize: 13,
                  height: 1.35,
                ),
              ),
            ),
            if (job.scoreReasons.isNotEmpty) ...[
              const SizedBox(height: 8),
              _DetailRows(
                rows: [
                  for (final reason in job.scoreReasons)
                    MapEntry('Reason', reason),
                ],
              ),
            ],
            for (final section in diagnosticSections) ...[
              const SizedBox(height: 12),
              _DetailSectionTitle(section.title),
              if (section.body != null && section.body!.trim().isNotEmpty)
                Text(
                  section.body!,
                  style: const TextStyle(
                    color: AtlasPalette.muted,
                    fontSize: 12,
                    height: 1.3,
                  ),
                ),
              if (section.rows.isNotEmpty)
                _DetailRows(
                  rows: section.rows
                      .take(30)
                      .map((row) => MapEntry(row.label, row.value))
                      .toList(growable: false),
                ),
            ],
          ],
        ),
      ],
    );
  }
}

class _FormattedDetailSectionView extends StatelessWidget {
  const _FormattedDetailSectionView({required this.section});

  final AtlasFormattedDetailSection section;

  @override
  Widget build(BuildContext context) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        _DetailSectionTitle(section.title),
        const SizedBox(height: 6),
        for (final block in section.blocks) ...[
          _FormattedDetailBlockView(block: block),
          const SizedBox(height: 8),
        ],
      ],
    );
  }
}

class _FormattedDetailBlockView extends StatelessWidget {
  const _FormattedDetailBlockView({required this.block});

  final AtlasDetailBlock block;

  @override
  Widget build(BuildContext context) {
    return switch (block) {
      AtlasDetailParagraphBlock(:final text) => Text(
        text,
        style: const TextStyle(
          color: AtlasPalette.ink,
          fontSize: 14,
          height: 1.38,
        ),
      ),
      AtlasDetailBulletsBlock(:final items) => Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          for (final item in items)
            Padding(
              padding: const EdgeInsets.only(bottom: 6),
              child: Row(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  const Text(
                    '•',
                    style: TextStyle(
                      color: AtlasPalette.accent,
                      fontSize: 14,
                      height: 1.38,
                      fontWeight: FontWeight.w800,
                    ),
                  ),
                  const SizedBox(width: 8),
                  Expanded(
                    child: Text(
                      item,
                      style: const TextStyle(
                        color: AtlasPalette.ink,
                        fontSize: 14,
                        height: 1.38,
                      ),
                    ),
                  ),
                ],
              ),
            ),
        ],
      ),
      AtlasDetailFactsBlock(:final facts) => _DetailRows(
        rows: facts
            .map((fact) => MapEntry(fact.label, fact.value))
            .toList(growable: false),
      ),
    };
  }
}

class _DetailSectionTitle extends StatelessWidget {
  const _DetailSectionTitle(this.text);

  final String text;

  @override
  Widget build(BuildContext context) {
    return Text(
      text,
      style: const TextStyle(
        color: AtlasPalette.ink,
        fontSize: 16,
        fontWeight: FontWeight.w800,
      ),
    );
  }
}

class _DetailRows extends StatelessWidget {
  const _DetailRows({required this.rows});

  final List<MapEntry<String, String>> rows;

  @override
  Widget build(BuildContext context) {
    return Column(
      children: [
        for (final row in rows.where((row) => row.value.trim().isNotEmpty))
          Padding(
            padding: const EdgeInsets.symmetric(vertical: 4),
            child: Row(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                SizedBox(
                  width: 112,
                  child: Text(
                    row.key,
                    maxLines: 2,
                    overflow: TextOverflow.ellipsis,
                    style: const TextStyle(
                      color: AtlasPalette.muted,
                      fontSize: 12,
                      fontWeight: FontWeight.w700,
                    ),
                  ),
                ),
                const SizedBox(width: 10),
                Expanded(
                  child: Text(
                    row.value,
                    style: const TextStyle(
                      color: AtlasPalette.ink,
                      fontSize: 13,
                      height: 1.28,
                    ),
                  ),
                ),
              ],
            ),
          ),
      ],
    );
  }
}

class _CopyLinkTile extends StatelessWidget {
  const _CopyLinkTile({required this.label, required this.url});

  final String label;
  final String url;

  @override
  Widget build(BuildContext context) {
    return ListTile(
      contentPadding: EdgeInsets.zero,
      dense: true,
      leading: const Icon(AtlasIcons.link, color: AtlasPalette.accent),
      title: Text(label),
      subtitle: Text(url, maxLines: 1, overflow: TextOverflow.ellipsis),
      trailing: Tooltip(
        message: 'Copy link',
        child: IconButton(
          onPressed: () {
            unawaited(Clipboard.setData(ClipboardData(text: url)));
            ScaffoldMessenger.of(
              context,
            ).showSnackBar(SnackBar(content: Text('$label copied')));
          },
          icon: const Icon(AtlasIcons.copy, size: 18),
        ),
      ),
    );
  }
}

class AtlasPlaceholderPanel extends StatelessWidget {
  const AtlasPlaceholderPanel({
    required this.title,
    required this.icon,
    required this.summary,
    super.key,
  });

  final String title;
  final IconData icon;
  final String summary;

  @override
  Widget build(BuildContext context) {
    return ListView(
      padding: const EdgeInsets.fromLTRB(20, 28, 20, 24),
      children: [
        Row(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Container(
              width: 40,
              height: 40,
              decoration: BoxDecoration(
                color: AtlasPalette.accent.withValues(alpha: 0.12),
                borderRadius: BorderRadius.circular(8),
              ),
              child: Icon(icon, color: AtlasPalette.accent),
            ),
            const SizedBox(width: 12),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(
                    title,
                    style: const TextStyle(
                      fontSize: 22,
                      fontWeight: FontWeight.w700,
                      color: AtlasPalette.ink,
                    ),
                  ),
                  const SizedBox(height: 6),
                  Text(
                    summary,
                    style: const TextStyle(
                      fontSize: 14,
                      height: 1.35,
                      color: AtlasPalette.muted,
                    ),
                  ),
                ],
              ),
            ),
          ],
        ),
      ],
    );
  }
}

class AtlasSettingsPanel extends StatefulWidget {
  AtlasSettingsPanel({
    AtlasAppController? controller,
    AtlasClientFactory? clientFactory,
    super.key,
  }) : controller =
           controller ?? AtlasAppController(clientFactory: clientFactory),
       ownsController = controller == null;

  final AtlasAppController controller;
  final bool ownsController;

  @override
  State<AtlasSettingsPanel> createState() => _AtlasSettingsPanelState();
}

class _AtlasSettingsPanelState extends State<AtlasSettingsPanel> {
  late TextEditingController _apiBaseURLController;
  double _refreshIntervalHours = 24;

  static const _refreshOptions = <(String, double)>[
    ('Every hour', 1),
    ('Every 6 hours', 6),
    ('Every 12 hours', 12),
    ('Every 24 hours', 24),
    ('Every 48 hours', 48),
    ('Weekly', 168),
  ];

  @override
  void initState() {
    super.initState();
    _apiBaseURLController = TextEditingController(
      text: _formatBaseURL(widget.controller.baseURL),
    );
    widget.controller.addListener(_handleControllerChanged);
  }

  @override
  void didUpdateWidget(covariant AtlasSettingsPanel oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (oldWidget.controller == widget.controller) {
      return;
    }
    oldWidget.controller.removeListener(_handleControllerChanged);
    if (oldWidget.ownsController) {
      oldWidget.controller.dispose();
    }
    _apiBaseURLController.text = _formatBaseURL(widget.controller.baseURL);
    widget.controller.addListener(_handleControllerChanged);
  }

  @override
  void dispose() {
    widget.controller.removeListener(_handleControllerChanged);
    if (widget.ownsController) {
      widget.controller.dispose();
    }
    _apiBaseURLController.dispose();
    super.dispose();
  }

  void _handleControllerChanged() {
    if (!mounted) {
      return;
    }
    setState(() {});
  }

  @override
  Widget build(BuildContext context) {
    final controller = widget.controller;
    final draftBaseURL = AtlasAPIClient.normalizedBaseURL(
      _apiBaseURLController.text,
    );

    return SingleChildScrollView(
      padding: const EdgeInsets.fromLTRB(16, 8, 16, 24),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          const _SettingsHeader(),
          const SizedBox(height: 22),
          if (controller.plaintextMigrationContext case final context?) ...[
            _SettingsSection(
              title: 'AtlasVault',
              children: <Widget>[
                AtlasVaultPlaintextMigrationPanel(
                  owner: context.owner,
                  platform: context.platform,
                ),
              ],
            ),
          ],
          if (controller.interoperabilityContext case final context?) ...[
            _SettingsSection(
              title: 'Encrypted Interoperability',
              children: <Widget>[
                AtlasVaultInteroperabilityPanel(owner: context.owner),
              ],
            ),
          ],
          if (controller.trustedPairingContext case final context?) ...[
            _SettingsSection(
              title: 'Trusted Devices',
              children: <Widget>[
                AtlasVaultTrustedPairingPanel(owner: context.owner),
              ],
            ),
          ],
          _SettingsSection(
            title: 'Server',
            children: [
              TextField(
                controller: _apiBaseURLController,
                keyboardType: TextInputType.url,
                textInputAction: TextInputAction.done,
                autocorrect: false,
                decoration: const InputDecoration(
                  labelText: 'API base URL',
                  prefixIcon: Icon(AtlasIcons.link),
                ),
                onChanged: (_) {
                  controller.clearConnectionMessage();
                },
              ),
              const SizedBox(height: 10),
              _SettingsValueRow(
                label: 'Saved server',
                value: _formatBaseURL(controller.baseURL),
              ),
              if (draftBaseURL != null &&
                  draftBaseURL != controller.baseURL) ...[
                const SizedBox(height: 6),
                _SettingsValueRow(
                  label: 'Editing',
                  value: _formatBaseURL(draftBaseURL),
                ),
                const SizedBox(height: 6),
                const Text(
                  'The saved server changes only after Save and Reload connects successfully.',
                  style: TextStyle(fontSize: 12, color: AtlasPalette.muted),
                ),
              ],
              const SizedBox(height: 12),
              Wrap(
                spacing: 10,
                runSpacing: 8,
                children: [
                  FilledButton.icon(
                    onPressed: controller.isTesting ? null : _testConnection,
                    icon: controller.isTesting
                        ? const SizedBox.square(
                            dimension: 16,
                            child: CircularProgressIndicator(strokeWidth: 2),
                          )
                        : const Icon(AtlasIcons.sources),
                    label: const Text('Test'),
                  ),
                  OutlinedButton.icon(
                    onPressed: controller.isSaving ? null : _saveAndReload,
                    icon: controller.isSaving
                        ? const SizedBox.square(
                            dimension: 16,
                            child: CircularProgressIndicator(strokeWidth: 2),
                          )
                        : const Icon(AtlasIcons.refresh),
                    label: const Text('Save and Reload'),
                  ),
                ],
              ),
            ],
          ),
          _SettingsSection(
            title: 'Status',
            children: [
              Text(
                controller.connectionStatus,
                style: const TextStyle(fontWeight: FontWeight.w700),
              ),
              if (controller.connectionMessage != null) ...[
                const SizedBox(height: 6),
                Text(
                  controller.connectionMessage!,
                  style: const TextStyle(
                    fontSize: 13,
                    color: AtlasPalette.muted,
                  ),
                ),
              ],
            ],
          ),
          _SettingsSection(
            title: 'Local Save',
            children: [
              _SettingsValueRow(
                label: 'Last updated',
                value: controller.cacheSavedAt == null
                    ? 'Never'
                    : _formatSavedAt(controller.cacheSavedAt!),
              ),
              const SizedBox(height: 6),
              _SettingsValueRow(
                label: 'Cache status',
                value: controller.cacheFreshnessLabel,
              ),
              const SizedBox(height: 6),
              _SettingsValueRow(
                label: 'Cached jobs',
                value: _formatCount(controller.cachedJobCount),
              ),
              const SizedBox(height: 6),
              _SettingsValueRow(
                label: 'Search total',
                value: controller.resultCountLabel,
              ),
              if (controller.healthSummary?.openJobs != null) ...[
                const SizedBox(height: 6),
                _SettingsValueRow(
                  label: 'Health open jobs',
                  value: _formatCount(controller.healthSummary!.openJobs!),
                ),
              ],
              if (controller.countReconciliationSummary != null) ...[
                const SizedBox(height: 8),
                Text(
                  controller.countReconciliationSummary!,
                  style: const TextStyle(
                    fontSize: 12,
                    height: 1.3,
                    color: AtlasPalette.muted,
                  ),
                ),
              ],
              const SizedBox(height: 6),
              _SettingsValueRow(
                label: 'Cached details',
                value: _formatCount(controller.cachedDetailCount),
              ),
              const SizedBox(height: 12),
              DropdownButtonFormField<double>(
                initialValue: _refreshIntervalHours,
                decoration: const InputDecoration(
                  labelText: 'Auto refresh',
                  prefixIcon: Icon(AtlasIcons.deadline),
                ),
                items: [
                  for (final option in _refreshOptions)
                    DropdownMenuItem(value: option.$2, child: Text(option.$1)),
                ],
                onChanged: (value) {
                  if (value == null) {
                    return;
                  }
                  setState(() {
                    _refreshIntervalHours = value;
                  });
                },
              ),
              const SizedBox(height: 12),
              OutlinedButton.icon(
                onPressed: controller.isRefreshingLocalSave
                    ? null
                    : _refreshLocalSave,
                icon: controller.isRefreshingLocalSave
                    ? const SizedBox.square(
                        dimension: 16,
                        child: CircularProgressIndicator(strokeWidth: 2),
                      )
                    : const Icon(AtlasIcons.saveOffline),
                label: const Text('Refresh Local Save Now'),
              ),
              const SizedBox(height: 8),
              TextButton.icon(
                onPressed: controller.cacheSavedAt == null
                    ? null
                    : _clearLocalCache,
                icon: const Icon(AtlasIcons.delete),
                label: const Text('Clear Local Cache'),
              ),
              const SizedBox(height: 8),
              const Text(
                'Refresh writes the latest open vacancies to this device. Cache is marked stale after 24 hours and retained for 7 days.',
                style: TextStyle(fontSize: 12, color: AtlasPalette.muted),
              ),
              if (controller.results.isNotEmpty) ...[
                const SizedBox(height: 12),
                const Text(
                  'Latest refreshed jobs',
                  style: TextStyle(
                    color: AtlasPalette.ink,
                    fontWeight: FontWeight.w800,
                  ),
                ),
                const SizedBox(height: 8),
                for (final job in controller.results.take(3))
                  Padding(
                    padding: const EdgeInsets.only(bottom: 6),
                    child: Row(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        const Icon(
                          AtlasIcons.contract,
                          size: 16,
                          color: AtlasPalette.accent,
                        ),
                        const SizedBox(width: 8),
                        Expanded(
                          child: Text(
                            job.title,
                            style: const TextStyle(
                              color: AtlasPalette.ink,
                              fontWeight: FontWeight.w600,
                            ),
                          ),
                        ),
                      ],
                    ),
                  ),
              ],
            ],
          ),
          const _SettingsSection(
            title: 'Android Setup',
            children: [
              Text(
                'Use http://10.253.1.43:8765 on the physical Pixel while job-api is running. Use http://10.0.2.2:8765 only on the Android emulator.',
                style: TextStyle(
                  fontSize: 13,
                  height: 1.35,
                  color: AtlasPalette.muted,
                ),
              ),
              SizedBox(height: 8),
              Text(
                'If a physical device cannot connect, confirm that the Mac firewall allows job-api and that the phone is on the same Wi-Fi network.',
                style: TextStyle(
                  fontSize: 13,
                  height: 1.35,
                  color: AtlasPalette.muted,
                ),
              ),
            ],
          ),
        ],
      ),
    );
  }

  Future<void> _testConnection() async {
    final baseURL = AtlasAPIClient.normalizedBaseURL(
      _apiBaseURLController.text,
    );
    if (baseURL == null) {
      widget.controller.reportValidationError(
        'Enter a valid http:// or https:// API base URL.',
      );
      return;
    }

    await widget.controller.testConnection(baseURL);
  }

  Future<void> _saveAndReload() async {
    final baseURL = AtlasAPIClient.normalizedBaseURL(
      _apiBaseURLController.text,
    );
    if (baseURL == null) {
      widget.controller.reportValidationError(
        'Enter a valid http:// or https:// API base URL.',
      );
      return;
    }

    await widget.controller.saveAndReload(baseURL);
    _apiBaseURLController.text = _formatBaseURL(widget.controller.baseURL);
  }

  Future<void> _refreshLocalSave() async {
    await widget.controller.refreshLocalSave();
  }

  Future<void> _clearLocalCache() async {
    await widget.controller.clearPersistedCache();
  }
}

class _SettingsHeader extends StatelessWidget {
  const _SettingsHeader();

  @override
  Widget build(BuildContext context) {
    return const Row(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        _SettingsIcon(AtlasIcons.settings),
        SizedBox(width: 12),
        Expanded(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(
                'Atlas Settings',
                style: TextStyle(
                  color: AtlasPalette.ink,
                  fontSize: 24,
                  fontWeight: FontWeight.w800,
                ),
              ),
              SizedBox(height: 4),
              Text(
                'Server connection, local save, and Android network setup.',
                style: TextStyle(
                  color: AtlasPalette.muted,
                  fontSize: 14,
                  height: 1.3,
                ),
              ),
            ],
          ),
        ),
      ],
    );
  }
}

class _SettingsSection extends StatelessWidget {
  const _SettingsSection({required this.title, required this.children});

  final String title;
  final List<Widget> children;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.only(bottom: 20),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(
            title,
            style: const TextStyle(
              color: AtlasPalette.ink,
              fontSize: 16,
              fontWeight: FontWeight.w800,
            ),
          ),
          const SizedBox(height: 10),
          ...children,
        ],
      ),
    );
  }
}

class _SettingsValueRow extends StatelessWidget {
  const _SettingsValueRow({required this.label, required this.value});

  final String label;
  final String value;

  @override
  Widget build(BuildContext context) {
    return Row(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        SizedBox(
          width: 128,
          child: Text(
            label,
            style: const TextStyle(
              color: AtlasPalette.muted,
              fontWeight: FontWeight.w600,
            ),
          ),
        ),
        Expanded(
          child: Text(
            value,
            style: const TextStyle(
              color: AtlasPalette.ink,
              fontWeight: FontWeight.w600,
            ),
          ),
        ),
      ],
    );
  }
}

class _SettingsIcon extends StatelessWidget {
  const _SettingsIcon(this.icon);

  final IconData icon;

  @override
  Widget build(BuildContext context) {
    return Container(
      width: 40,
      height: 40,
      decoration: BoxDecoration(
        color: AtlasPalette.accent.withValues(alpha: 0.12),
        borderRadius: BorderRadius.circular(8),
      ),
      child: Icon(icon, color: AtlasPalette.accent),
    );
  }
}

class AtlasFilterChip extends StatelessWidget {
  const AtlasFilterChip({
    required this.label,
    required this.icon,
    this.selected = false,
    this.onTap,
    this.onDeleted,
    super.key,
  });

  final String label;
  final IconData icon;
  final bool selected;
  final VoidCallback? onTap;
  final VoidCallback? onDeleted;

  @override
  Widget build(BuildContext context) {
    final foreground = selected ? Colors.white : AtlasPalette.ink;
    return Material(
      color: Colors.transparent,
      child: InkWell(
        onTap: onTap,
        borderRadius: BorderRadius.circular(999),
        child: Container(
          height: 32,
          padding: EdgeInsets.only(left: 9, right: onDeleted == null ? 9 : 5),
          decoration: BoxDecoration(
            color: selected ? AtlasPalette.accent : Colors.white,
            borderRadius: BorderRadius.circular(999),
            border: Border.all(
              color: selected ? AtlasPalette.accent : AtlasPalette.border,
            ),
          ),
          child: Row(
            mainAxisSize: MainAxisSize.min,
            children: [
              Icon(icon, size: 15, color: foreground),
              const SizedBox(width: 5),
              Text(
                label,
                style: TextStyle(
                  color: foreground,
                  fontSize: 12,
                  fontWeight: selected ? FontWeight.w800 : FontWeight.w600,
                ),
              ),
              if (onDeleted != null) ...[
                const SizedBox(width: 3),
                GestureDetector(
                  onTap: onDeleted,
                  child: Icon(AtlasIcons.close, size: 14, color: foreground),
                ),
              ],
            ],
          ),
        ),
      ),
    );
  }
}

enum AtlasMobileTab { search, saved, updates, sources, settings }

extension AtlasMobileTabTitle on AtlasMobileTab {
  String get title {
    return switch (this) {
      AtlasMobileTab.search => 'Search',
      AtlasMobileTab.saved => 'Saved',
      AtlasMobileTab.updates => 'Updates',
      AtlasMobileTab.sources => 'Sources',
      AtlasMobileTab.settings => 'Settings',
    };
  }
}

final class _QuickFilter {
  const _QuickFilter(this.label, this.icon);

  final String label;
  final IconData icon;
}

abstract final class AtlasIcons {
  static const search = CupertinoIcons.search;
  static const filter = CupertinoIcons.slider_horizontal_3;
  static const bookmark = CupertinoIcons.bookmark;
  static const bookmarkFilled = CupertinoIcons.bookmark_fill;
  static const deadline = CupertinoIcons.clock;
  static const location = CupertinoIcons.location;
  static const country = CupertinoIcons.globe;
  static const organization = CupertinoIcons.building_2_fill;
  static const contract = CupertinoIcons.briefcase;
  static const remote = CupertinoIcons.house;
  static const target = CupertinoIcons.scope;
  static const updates = CupertinoIcons.time;
  static const sources = CupertinoIcons.square_stack_3d_up;
  static const settings = CupertinoIcons.settings;
  static const settingsFilled = CupertinoIcons.settings_solid;
  static const chevron = CupertinoIcons.chevron_right;
  static const info = CupertinoIcons.info_circle;
  static const warning = CupertinoIcons.exclamationmark_triangle;
  static const refresh = CupertinoIcons.arrow_clockwise;
  static const close = CupertinoIcons.xmark;
  static const check = CupertinoIcons.check_mark_circled_solid;
  static const circle = CupertinoIcons.circle;
  static const sort = CupertinoIcons.arrow_up_arrow_down;
  static const saveOffline = CupertinoIcons.arrow_down_doc;
  static const copy = CupertinoIcons.doc_on_doc;
  static const link = CupertinoIcons.link;
  static const delete = CupertinoIcons.trash;
}

abstract final class AtlasPalette {
  static const accent = Color(0xFF008CC7);
  static const strategyOrange = Color(0xFFE86E14);
  static const deadlineAmber = Color(0xFFD98C14);
  static const deadlineRed = Color(0xFFC72924);
  static const success = Color(0xFF238636);
  static const ink = Color(0xFF1D252D);
  static const muted = Color(0xFF5F6B76);
  static const border = Color(0xFFD9E2EA);
  static const background = Color(0xFFF7FAFC);
}
