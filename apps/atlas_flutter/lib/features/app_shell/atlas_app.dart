import 'dart:async';

import 'package:atlas/atlas.dart';
import 'package:flutter/material.dart';

typedef AtlasClientFactory = AtlasAPIClient Function(Uri baseURL);

class AtlasAppController extends ChangeNotifier {
  AtlasAppController({Uri? initialBaseURL, AtlasClientFactory? clientFactory})
    : baseURL = initialBaseURL ?? Uri.parse('http://10.253.1.43:8765'),
      _clientFactory =
          clientFactory ?? ((baseURL) => AtlasAPIClient(baseURL: baseURL));

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
  int total = 0;
  int cachedJobCount = 0;
  DateTime? cacheSavedAt;
  Timer? _searchDebounce;
  int _savedSearchSequence = 0;

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

  @override
  void dispose() {
    _searchDebounce?.cancel();
    super.dispose();
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
    isTesting = true;
    connectionMessage = null;
    notifyListeners();
    try {
      final client = _clientFactory(candidateBaseURL);
      final health = await client.health();
      connectionStatus = 'Connected';
      connectionMessage = _healthMessage(health);
      await _loadSavedSearches(client);
    } catch (error) {
      connectionStatus = 'Not connected';
      connectionMessage = 'Connection failed: $error';
    } finally {
      isTesting = false;
      notifyListeners();
    }
  }

  Future<void> saveAndReload(Uri candidateBaseURL) async {
    isSaving = true;
    connectionMessage = null;
    notifyListeners();
    try {
      final client = _clientFactory(candidateBaseURL);
      await client.health();
      baseURL = candidateBaseURL;
      connectionStatus = 'Connected';
      final refreshed = await _refreshSearch(client);
      await _loadSavedSearches(client);
      connectionMessage =
          'Saved ${_formatBaseURL(candidateBaseURL)} and refreshed $refreshed ${_jobWord(refreshed)}.';
    } catch (error) {
      connectionStatus = 'Not connected';
      connectionMessage = 'Save failed: $error';
    } finally {
      isSaving = false;
      notifyListeners();
    }
  }

  Future<void> refreshLocalSave() async {
    isRefreshingLocalSave = true;
    connectionMessage = null;
    notifyListeners();
    try {
      final client = _clientFactory(baseURL);
      final refreshed = await _refreshSearch(client);
      await _loadSavedSearches(client);
      connectionStatus = 'Connected';
      connectionMessage =
          'Local save refreshed: $refreshed ${_jobWord(refreshed)} cached for this session.';
    } catch (error) {
      connectionStatus = 'Not connected';
      connectionMessage = 'Local save refresh failed: $error';
    } finally {
      isRefreshingLocalSave = false;
      notifyListeners();
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

  Future<void> saveCurrentSearch() async {
    isSavingSearch = true;
    connectionMessage = null;
    notifyListeners();
    final request = _currentSearchRequest();
    final name = _nextSavedSearchName();
    final summary = _savedSearchSummary(request);
    try {
      final client = _clientFactory(baseURL);
      final savedSearch = await client.saveSearch(
        name: name,
        request: request,
        summary: summary,
      );
      _upsertSavedSearch(savedSearch);
      connectionStatus = 'Connected';
      connectionMessage = 'Saved ${savedSearch.name} locally.';
    } catch (error) {
      connectionMessage = 'Save search failed: $error';
    } finally {
      isSavingSearch = false;
      notifyListeners();
    }
  }

  Future<void> runSavedSearch(AtlasSavedSearch search) async {
    query = search.request.text ?? '';
    filters = _filtersFromRequest(search.request);
    sortOrder = SortOrder.fromAPIValue(search.request.sort);
    notifyListeners();
    await _refreshIfReady();
  }

  Future<int> _refreshSearch(AtlasAPIClient client) async {
    isSearching = true;
    notifyListeners();
    try {
      final response = await client.search(_currentSearchRequest());
      results = List.unmodifiable(response.results);
      total = response.total;
      cachedJobCount = response.results.length;
      cacheSavedAt = DateTime.now();
      return response.results.length;
    } finally {
      isSearching = false;
    }
  }

  Future<void> _loadSavedSearches(AtlasAPIClient client) async {
    try {
      savedSearches = List.unmodifiable(await client.savedSearches());
      _syncSavedSearchSequence();
    } catch (_) {
      // Saved-search persistence is not required for health/search success.
    }
  }

  AtlasSearchRequest _currentSearchRequest() {
    return AtlasSearchRequest.fromFilters(
      filters: filters,
      query: query,
      sortOrder: sortOrder,
      limit: 50,
    );
  }

  Future<void> _refreshIfReady() async {
    if (cacheSavedAt != null || connectionStatus == 'Connected') {
      await refreshLocalSave();
    }
  }

  void _scheduleSearchIfReady() {
    _searchDebounce?.cancel();
    if (cacheSavedAt == null && connectionStatus != 'Connected') {
      return;
    }
    _searchDebounce = Timer(const Duration(milliseconds: 350), () {
      refreshLocalSave();
    });
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

  void _upsertSavedSearch(AtlasSavedSearch savedSearch) {
    final remaining = savedSearches
        .where((existing) => existing.name != savedSearch.name)
        .toList(growable: false);
    savedSearches = List.unmodifiable([savedSearch, ...remaining]);
    _syncSavedSearchSequence();
  }
}

AtlasSearchFilters _filtersFromRequest(AtlasSearchRequest request) {
  return AtlasSearchFilters(
    openOnly: request.status.contains('open'),
    city: request.cities.isEmpty ? '' : request.cities.first,
    countryISO3: request.countriesISO3.isEmpty
        ? ''
        : request.countriesISO3.first,
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

String _jobWord(int count) => count == 1 ? 'job' : 'jobs';

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

  @override
  void initState() {
    super.initState();
    _controller = widget.controller ?? AtlasAppController();
    _ownsController = widget.controller == null;
  }

  @override
  void dispose() {
    if (_ownsController) {
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
            const AtlasPlaceholderPanel(
              title: 'Source Updates',
              icon: Icons.history,
              summary:
                  'Recent source refresh runs will show fetched, inserted, updated, missing, and closed counts.',
            ),
            const AtlasPlaceholderPanel(
              title: 'Source Health',
              icon: Icons.settings_input_antenna,
              summary:
                  'Each source will show health, open jobs, total jobs, and last-seen status.',
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
            icon: Icon(Icons.search),
            selectedIcon: Icon(Icons.search),
            label: 'Search',
          ),
          NavigationDestination(
            icon: Icon(Icons.bookmark_border),
            selectedIcon: Icon(Icons.bookmark),
            label: 'Saved',
          ),
          NavigationDestination(
            icon: Icon(Icons.history),
            selectedIcon: Icon(Icons.history),
            label: 'Updates',
          ),
          NavigationDestination(
            icon: Icon(Icons.settings_input_antenna),
            selectedIcon: Icon(Icons.settings_input_antenna),
            label: 'Sources',
          ),
          NavigationDestination(
            icon: Icon(Icons.settings_outlined),
            selectedIcon: Icon(Icons.settings),
            label: 'Settings',
          ),
        ],
      ),
    );
  }

  void _showFilterSheet() {
    showModalBottomSheet<void>(
      context: context,
      showDragHandle: true,
      builder: (context) => AtlasFilterSheet(controller: _controller),
    );
  }
}

class AtlasSearchSkeleton extends StatelessWidget {
  const AtlasSearchSkeleton({required this.controller, super.key});

  final AtlasAppController controller;

  static const _quickFilters = [
    _QuickFilter('Closing soon', Icons.schedule),
    _QuickFilter('Remote', Icons.home_work_outlined),
    _QuickFilter('Best fit', Icons.track_changes),
  ];

  @override
  Widget build(BuildContext context) {
    return AnimatedBuilder(
      animation: controller,
      builder: (context, _) {
        return ListView(
          padding: const EdgeInsets.fromLTRB(16, 8, 16, 24),
          children: [
            TextField(
              textInputAction: TextInputAction.search,
              decoration: const InputDecoration(
                prefixIcon: Icon(Icons.search),
                labelText: 'Title, keyword, skill, or organization',
              ),
              onChanged: (value) {
                controller.updateQuery(value);
              },
              onSubmitted: (_) {
                controller.refreshLocalSave();
              },
            ),
            const SizedBox(height: 12),
            SizedBox(
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
                      icon: Icons.check_circle_outline,
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
            ),
            const SizedBox(height: 14),
            AtlasSearchStatusBar(controller: controller),
            if (controller.connectionMessage != null &&
                controller.connectionStatus != 'Connected') ...[
              const SizedBox(height: 12),
              AtlasStatusBanner(message: controller.connectionMessage!),
            ],
            const SizedBox(height: 10),
            if (controller.results.isEmpty)
              const AtlasEmptySearchState()
            else
              ...controller.results.map((job) => AtlasJobResultTile(job)),
          ],
        );
      },
    );
  }
}

class AtlasJobResultTile extends StatelessWidget {
  const AtlasJobResultTile(this.job, {super.key});

  final JobSearchResult job;

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
              builder: (_) => AtlasJobDetailScreen(job: job),
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
              const Padding(
                padding: EdgeInsets.only(top: 17),
                child: Icon(
                  Icons.chevron_right,
                  size: 18,
                  color: AtlasPalette.muted,
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
        color: AtlasPalette.accent.withValues(alpha: 0.14),
        borderRadius: BorderRadius.circular(8),
      ),
      child: Text(
        job.sourceInitials,
        maxLines: 1,
        overflow: TextOverflow.clip,
        style: const TextStyle(
          color: AtlasPalette.accent,
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
          const Icon(Icons.info_outline, color: AtlasPalette.accent, size: 18),
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
                      ? Icons.wifi
                      : Icons.wifi_off,
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
                '${controller.total} results',
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
              icon: const Icon(Icons.swap_vert, size: 18),
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
                    ? const Icon(Icons.check, size: 18)
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
            Icon(Icons.manage_search, size: 48, color: AtlasPalette.accent),
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
                        ? Icons.tune
                        : Icons.tune_outlined,
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
                        ? Icons.hourglass_empty
                        : controller.savedSearches.isEmpty
                        ? Icons.bookmark_border
                        : Icons.bookmark,
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

class AtlasFilterSheet extends StatelessWidget {
  const AtlasFilterSheet({required this.controller, super.key});

  final AtlasAppController controller;

  @override
  Widget build(BuildContext context) {
    return AnimatedBuilder(
      animation: controller,
      builder: (context, _) {
        return SafeArea(
          child: ListView(
            shrinkWrap: true,
            padding: const EdgeInsets.fromLTRB(20, 0, 20, 20),
            children: [
              Row(
                children: [
                  const Expanded(
                    child: Text(
                      'Filters',
                      style: TextStyle(
                        color: AtlasPalette.ink,
                        fontSize: 20,
                        fontWeight: FontWeight.w800,
                      ),
                    ),
                  ),
                  Tooltip(
                    message: 'Close filters',
                    child: IconButton(
                      onPressed: () {
                        Navigator.of(context).pop();
                      },
                      icon: const Icon(Icons.close),
                    ),
                  ),
                ],
              ),
              SwitchListTile(
                contentPadding: EdgeInsets.zero,
                title: const Text('Open only'),
                subtitle: const Text('Hide closed and history rows'),
                value: controller.filters.openOnly,
                onChanged: (value) {
                  controller.setOpenOnly(value);
                },
              ),
              SwitchListTile(
                contentPadding: EdgeInsets.zero,
                title: const Text('Closing soon'),
                value: controller.filters.closingSoon,
                onChanged: (_) {
                  controller.toggleQuickFilter('Closing soon');
                },
              ),
              SwitchListTile(
                contentPadding: EdgeInsets.zero,
                title: const Text('Remote'),
                value: controller.filters.isRemoteOnly,
                onChanged: (_) {
                  controller.toggleQuickFilter('Remote');
                },
              ),
              SwitchListTile(
                contentPadding: EdgeInsets.zero,
                title: const Text('Best fit'),
                value: controller.sortOrder == SortOrder.bestFit,
                onChanged: (_) {
                  controller.toggleQuickFilter('Best fit');
                },
              ),
            ],
          ),
        );
      },
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
        if (controller.savedSearches.isEmpty) {
          return const AtlasPlaceholderPanel(
            title: 'Saved Searches',
            icon: Icons.bookmark_border,
            summary:
                'Saved searches and tracked applications will appear here.',
          );
        }
        return ListView(
          padding: const EdgeInsets.fromLTRB(16, 16, 16, 24),
          children: [
            const Text(
              'Saved Searches',
              style: TextStyle(
                color: AtlasPalette.ink,
                fontSize: 22,
                fontWeight: FontWeight.w800,
              ),
            ),
            const SizedBox(height: 10),
            for (final search in controller.savedSearches)
              ListTile(
                contentPadding: EdgeInsets.zero,
                leading: const Icon(Icons.bookmark, color: AtlasPalette.accent),
                title: Text(search.name),
                subtitle: Text(search.description ?? 'Saved search'),
                trailing: const Icon(Icons.chevron_right),
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

class AtlasJobDetailScreen extends StatelessWidget {
  const AtlasJobDetailScreen({required this.job, super.key});

  final JobSearchResult job;

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('Job Detail')),
      body: ListView(
        padding: const EdgeInsets.fromLTRB(16, 16, 16, 24),
        children: [
          Text(
            job.title,
            style: const TextStyle(
              color: AtlasPalette.ink,
              fontSize: 22,
              height: 1.15,
              fontWeight: FontWeight.w800,
            ),
          ),
          const SizedBox(height: 8),
          Text(
            '${job.organizationDisplay} · ${job.dutyStation}',
            style: const TextStyle(color: AtlasPalette.muted, fontSize: 14),
          ),
          const SizedBox(height: 12),
          Wrap(
            spacing: 8,
            runSpacing: 8,
            children: [
              AtlasDeadlinePill(job: job),
              AtlasMetadataPill(
                icon: Icons.badge_outlined,
                label: job.gradeCode.isEmpty ? 'Grade unknown' : job.gradeCode,
              ),
              AtlasMetadataPill(
                icon: Icons.work_outline,
                label: job.contractLabel,
              ),
              AtlasMetadataPill(
                icon: Icons.place_outlined,
                label: job.workModality,
              ),
            ],
          ),
          const SizedBox(height: 20),
          const Text(
            'Why this matched',
            style: TextStyle(
              color: AtlasPalette.ink,
              fontSize: 16,
              fontWeight: FontWeight.w800,
            ),
          ),
          const SizedBox(height: 6),
          Text(
            job.matchSummary,
            style: const TextStyle(
              color: AtlasPalette.ink,
              fontSize: 14,
              height: 1.35,
            ),
          ),
          const SizedBox(height: 20),
          const Text(
            'Preview',
            style: TextStyle(
              color: AtlasPalette.ink,
              fontSize: 16,
              fontWeight: FontWeight.w800,
            ),
          ),
          const SizedBox(height: 6),
          Text(
            job.description,
            style: const TextStyle(
              color: AtlasPalette.muted,
              fontSize: 14,
              height: 1.35,
            ),
          ),
        ],
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
                  prefixIcon: Icon(Icons.link),
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
                        : const Icon(Icons.hub_outlined),
                    label: const Text('Test'),
                  ),
                  OutlinedButton.icon(
                    onPressed: controller.isSaving ? null : _saveAndReload,
                    icon: controller.isSaving
                        ? const SizedBox.square(
                            dimension: 16,
                            child: CircularProgressIndicator(strokeWidth: 2),
                          )
                        : const Icon(Icons.refresh),
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
                label: 'Cached jobs',
                value: controller.cachedJobCount.toString(),
              ),
              const SizedBox(height: 6),
              const _SettingsValueRow(label: 'Cached details', value: '0 / 0'),
              const SizedBox(height: 12),
              DropdownButtonFormField<double>(
                initialValue: _refreshIntervalHours,
                decoration: const InputDecoration(
                  labelText: 'Auto refresh',
                  prefixIcon: Icon(Icons.schedule),
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
                    : const Icon(Icons.download_for_offline_outlined),
                label: const Text('Refresh Local Save Now'),
              ),
              const SizedBox(height: 8),
              const Text(
                'Refresh pulls the latest open vacancies from the saved server so Search can work immediately.',
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
                          Icons.work_outline,
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
}

class _SettingsHeader extends StatelessWidget {
  const _SettingsHeader();

  @override
  Widget build(BuildContext context) {
    return const Row(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        _SettingsIcon(Icons.settings_outlined),
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
                  child: Icon(Icons.close, size: 14, color: foreground),
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
