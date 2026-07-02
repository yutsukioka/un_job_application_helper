import 'package:atlas/atlas.dart';
import 'package:flutter/material.dart';

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
  const AtlasHomeShell({super.key});

  @override
  State<AtlasHomeShell> createState() => _AtlasHomeShellState();
}

class _AtlasHomeShellState extends State<AtlasHomeShell> {
  AtlasMobileTab _selectedTab = AtlasMobileTab.search;

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: const Text('Atlas'),
        actions: _selectedTab == AtlasMobileTab.search
            ? const [
                Tooltip(
                  message: 'Filters',
                  child: IconButton(onPressed: null, icon: Icon(Icons.tune)),
                ),
                Tooltip(
                  message: 'Save search',
                  child: IconButton(
                    onPressed: null,
                    icon: Icon(Icons.bookmark_border),
                  ),
                ),
                SizedBox(width: 4),
              ]
            : null,
      ),
      body: SafeArea(
        top: false,
        child: IndexedStack(
          index: _selectedTab.index,
          children: [
            const AtlasSearchSkeleton(),
            const AtlasPlaceholderPanel(
              title: 'Saved Jobs',
              icon: Icons.bookmark_border,
              summary:
                  'Saved searches and tracked applications will appear here.',
            ),
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
            AtlasSettingsPanel(),
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
}

class AtlasSearchSkeleton extends StatelessWidget {
  const AtlasSearchSkeleton({super.key});

  static const _quickFilters = [
    _QuickFilter('Closing soon', Icons.schedule),
    _QuickFilter('Remote', Icons.home_work_outlined),
    _QuickFilter('Best fit', Icons.track_changes),
  ];

  @override
  Widget build(BuildContext context) {
    return ListView(
      padding: const EdgeInsets.fromLTRB(16, 8, 16, 24),
      children: [
        const TextField(
          textInputAction: TextInputAction.search,
          decoration: InputDecoration(
            prefixIcon: Icon(Icons.search),
            labelText: 'Title, keyword, skill, or organization',
          ),
        ),
        const SizedBox(height: 12),
        SizedBox(
          height: 36,
          child: ListView.separated(
            scrollDirection: Axis.horizontal,
            itemCount: _quickFilters.length + 1,
            separatorBuilder: (_, _) => const SizedBox(width: 8),
            itemBuilder: (context, index) {
              if (index == 0) {
                return const AtlasFilterChip(
                  label: 'Open only',
                  icon: Icons.check_circle_outline,
                  selected: true,
                );
              }
              final filter = _quickFilters[index - 1];
              return AtlasFilterChip(label: filter.label, icon: filter.icon);
            },
          ),
        ),
        const SizedBox(height: 14),
        const AtlasSearchStatusBar(),
        const SizedBox(height: 32),
        const AtlasEmptySearchState(),
      ],
    );
  }
}

class AtlasSearchStatusBar extends StatelessWidget {
  const AtlasSearchStatusBar({super.key});

  @override
  Widget build(BuildContext context) {
    return Row(
      children: [
        const SizedBox.square(
          dimension: 18,
          child: Icon(
            Icons.wifi_off,
            size: 18,
            color: AtlasPalette.deadlineAmber,
          ),
        ),
        const SizedBox(width: 8),
        const Expanded(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(
                '0 results',
                style: TextStyle(fontSize: 14, fontWeight: FontWeight.w700),
              ),
              SizedBox(height: 2),
              Text(
                'Offline until API connection is configured',
                maxLines: 1,
                overflow: TextOverflow.ellipsis,
                style: TextStyle(fontSize: 12, color: AtlasPalette.muted),
              ),
            ],
          ),
        ),
        MenuAnchor(
          builder: (context, controller, child) {
            return TextButton.icon(
              onPressed: controller.open,
              icon: const Icon(Icons.swap_vert, size: 18),
              label: Text('Sort: ${SortOrder.closingSoon.label}'),
            );
          },
          menuChildren: [
            for (final order in SortOrder.values)
              MenuItemButton(
                onPressed: () {},
                leadingIcon: order == SortOrder.closingSoon
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
    AtlasAPIClient Function(Uri baseURL)? clientFactory,
    super.key,
  }) : clientFactory =
           clientFactory ?? ((baseURL) => AtlasAPIClient(baseURL: baseURL));

  final AtlasAPIClient Function(Uri baseURL) clientFactory;

  @override
  State<AtlasSettingsPanel> createState() => _AtlasSettingsPanelState();
}

class _AtlasSettingsPanelState extends State<AtlasSettingsPanel> {
  late Uri _savedBaseURL;
  late TextEditingController _apiBaseURLController;
  String _status = 'Not connected';
  String? _connectionMessage;
  bool _isTesting = false;
  bool _isSaving = false;
  bool _isRefreshingLocalSave = false;
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
    _savedBaseURL = Uri.parse('http://10.253.1.43:8765');
    _apiBaseURLController = TextEditingController(
      text: _formatBaseURL(_savedBaseURL),
    );
  }

  @override
  void dispose() {
    _apiBaseURLController.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
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
                  setState(() {
                    _connectionMessage = null;
                  });
                },
              ),
              const SizedBox(height: 10),
              _SettingsValueRow(
                label: 'Saved server',
                value: _formatBaseURL(_savedBaseURL),
              ),
              if (draftBaseURL != null && draftBaseURL != _savedBaseURL) ...[
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
                    onPressed: _isTesting ? null : _testConnection,
                    icon: _isTesting
                        ? const SizedBox.square(
                            dimension: 16,
                            child: CircularProgressIndicator(strokeWidth: 2),
                          )
                        : const Icon(Icons.hub_outlined),
                    label: const Text('Test'),
                  ),
                  OutlinedButton.icon(
                    onPressed: _isSaving ? null : _saveAndReload,
                    icon: _isSaving
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
                _status,
                style: const TextStyle(fontWeight: FontWeight.w700),
              ),
              if (_connectionMessage != null) ...[
                const SizedBox(height: 6),
                Text(
                  _connectionMessage!,
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
              const _SettingsValueRow(label: 'Last updated', value: 'Never'),
              const SizedBox(height: 6),
              const _SettingsValueRow(label: 'Cached jobs', value: '0'),
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
                onPressed: _isRefreshingLocalSave ? null : _refreshLocalSave,
                icon: _isRefreshingLocalSave
                    ? const SizedBox.square(
                        dimension: 16,
                        child: CircularProgressIndicator(strokeWidth: 2),
                      )
                    : const Icon(Icons.download_for_offline_outlined),
                label: const Text('Refresh Local Save Now'),
              ),
              const SizedBox(height: 8),
              const Text(
                'The app will use cached vacancies as soon as the local save implementation lands.',
                style: TextStyle(fontSize: 12, color: AtlasPalette.muted),
              ),
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
      setState(() {
        _status = 'Not connected';
        _connectionMessage = 'Enter a valid http:// or https:// API base URL.';
      });
      return;
    }

    setState(() {
      _isTesting = true;
      _connectionMessage = null;
    });
    try {
      final health = await widget.clientFactory(baseURL).health();
      setState(() {
        _status = 'Connected';
        _connectionMessage = _healthMessage(health);
      });
    } catch (error) {
      setState(() {
        _status = 'Not connected';
        _connectionMessage = 'Connection failed: $error';
      });
    } finally {
      if (mounted) {
        setState(() {
          _isTesting = false;
        });
      }
    }
  }

  Future<void> _saveAndReload() async {
    final baseURL = AtlasAPIClient.normalizedBaseURL(
      _apiBaseURLController.text,
    );
    if (baseURL == null) {
      setState(() {
        _status = 'Not connected';
        _connectionMessage = 'Enter a valid http:// or https:// API base URL.';
      });
      return;
    }

    setState(() {
      _isSaving = true;
      _connectionMessage = null;
    });
    try {
      await widget.clientFactory(baseURL).health();
      setState(() {
        _savedBaseURL = baseURL;
        _apiBaseURLController.text = _formatBaseURL(baseURL);
        _status = 'Connected';
        _connectionMessage =
            'Saved ${_formatBaseURL(baseURL)} and reloaded search data.';
      });
    } catch (error) {
      setState(() {
        _status = 'Not connected';
        _connectionMessage = 'Save failed: $error';
      });
    } finally {
      if (mounted) {
        setState(() {
          _isSaving = false;
        });
      }
    }
  }

  Future<void> _refreshLocalSave() async {
    setState(() {
      _isRefreshingLocalSave = true;
      _connectionMessage = null;
    });
    await Future<void>.delayed(Duration.zero);
    if (!mounted) {
      return;
    }
    setState(() {
      _isRefreshingLocalSave = false;
      _connectionMessage =
          'Local save refresh will run after the offline cache slice is implemented.';
    });
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
    super.key,
  });

  final String label;
  final IconData icon;
  final bool selected;

  @override
  Widget build(BuildContext context) {
    final foreground = selected ? Colors.white : AtlasPalette.ink;
    return Container(
      height: 36,
      padding: const EdgeInsets.symmetric(horizontal: 10),
      decoration: BoxDecoration(
        color: selected ? AtlasPalette.accent : Colors.white,
        borderRadius: BorderRadius.circular(8),
        border: Border.all(
          color: selected ? AtlasPalette.accent : AtlasPalette.border,
        ),
      ),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          Icon(icon, size: 16, color: foreground),
          const SizedBox(width: 6),
          Text(
            label,
            style: TextStyle(
              color: foreground,
              fontWeight: selected ? FontWeight.w700 : FontWeight.w600,
            ),
          ),
        ],
      ),
    );
  }
}

enum AtlasMobileTab { search, saved, updates, sources, settings }

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
  static const ink = Color(0xFF1D252D);
  static const muted = Color(0xFF5F6B76);
  static const border = Color(0xFFD9E2EA);
  static const background = Color(0xFFF7FAFC);
}
