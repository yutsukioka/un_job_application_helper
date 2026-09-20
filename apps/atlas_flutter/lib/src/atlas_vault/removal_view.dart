import 'package:flutter/foundation.dart';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import 'sync_queue.dart';
import 'device_identity.dart';

AtlasVaultRemovalController atlasVaultPlatformRemovalController({
  required AtlasVaultRevocationRegistry registry,
  required AtlasVaultDeviceIdentity identity,
  required AtlasVaultGuardedSyncState history,
}) => AtlasVaultRemovalController(
  registry: registry,
  initiator: identity.descriptor.deviceId,
  history: history,
  authorize: AtlasVaultDeviceRemovalAuthorizer().authorize,
  sign: identity.signBytes,
);

/// Separate command and prompt purpose; no reuse of pairing/login authorization.
final class AtlasVaultDeviceRemovalAuthorizer {
  AtlasVaultDeviceRemovalAuthorizer({TargetPlatform? platform, this._channel})
    : _platform = platform ?? defaultTargetPlatform;
  final TargetPlatform _platform;
  final MethodChannel? _channel;
  Future<bool> authorize() async {
    final name = switch (_platform) {
      TargetPlatform.android => 'atlas/vault_android',
      TargetPlatform.windows => 'atlas/vault_windows',
      _ => null,
    };
    if (name == null) return false;
    try {
      final result = await (_channel ?? MethodChannel(name))
          .invokeMethod<Object?>('authorizeDeviceRemoval')
          .timeout(const Duration(seconds: 60));
      return result is bool && result;
    } catch (_) {
      return false;
    }
  }

  @override
  String toString() => 'AtlasVaultDeviceRemovalAuthorizer(<redacted>)';
}

/// P7 removal surface; application navigation and rotation progress belong to P8/C26.
final class AtlasVaultDeviceRemovalView extends StatefulWidget {
  const AtlasVaultDeviceRemovalView({
    super.key,
    required this.controller,
    required this.targetDevice,
    required this.keyEpoch,
  });
  final AtlasVaultRemovalController controller;
  final String targetDevice;
  final int keyEpoch;
  @override
  State<AtlasVaultDeviceRemovalView> createState() =>
      _AtlasVaultDeviceRemovalViewState();
}

final class _AtlasVaultDeviceRemovalViewState
    extends State<AtlasVaultDeviceRemovalView> {
  bool _confirmed = false, _busy = false;
  String _status = 'Loading';
  String _root = '';
  int _loadGeneration = 0;
  @override
  void initState() {
    super.initState();
    _load();
  }

  @override
  void didUpdateWidget(covariant AtlasVaultDeviceRemovalView oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (oldWidget.controller != widget.controller ||
        oldWidget.targetDevice != widget.targetDevice ||
        oldWidget.keyEpoch != widget.keyEpoch) {
      oldWidget.controller.cancel();
      _confirmed = false;
      _load();
    }
  }

  Future<void> _load() async {
    final owner = ++_loadGeneration;
    try {
      if (!RegExp(r'^avd1-[0-9a-f]{64}$').hasMatch(widget.targetDevice) ||
          widget.keyEpoch <= 0 ||
          widget.keyEpoch >= 9007199254740991) {
        throw const AtlasVaultRevocationException();
      }
      widget.controller.select(widget.targetDevice);
      final state = await widget.controller.registry.snapshot();
      if (!mounted || owner != _loadGeneration) return;
      setState(() {
        _status = state['status']! as String;
        _root = state['root']! as String;
      });
    } catch (_) {
      if (mounted && owner == _loadGeneration) {
        setState(() {
          _status = 'RECOVERY_PENDING';
        });
      }
    }
  }

  Future<void> _remove() async {
    if (!_confirmed || _busy || _status != 'ACTIVE') return;
    final controller = widget.controller, target = widget.targetDevice;
    setState(() {
      _busy = true;
    });
    try {
      await controller.remove(target);
      if (mounted) {
        setState(() {
          _status = 'REVOCATION_PENDING';
        });
      }
    } catch (_) {
      if (mounted) {
        setState(() {
          _status = 'Removal not authorized';
        });
      }
    } finally {
      if (mounted) {
        setState(() {
          _busy = false;
          _confirmed = false;
        });
      }
    }
  }

  @override
  void dispose() {
    _loadGeneration++;
    widget.controller.cancel();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) => Scaffold(
    appBar: AppBar(title: const Text('Remove Device')),
    body: ListView(
      padding: const EdgeInsets.all(20),
      children: [
        SelectableText(
          RegExp(r'^avd1-[0-9a-f]{64}$').hasMatch(widget.targetDevice)
              ? widget.targetDevice
              : 'Invalid device',
        ),
        const SizedBox(height: 16),
        Text('Registry: $_status'),
        if (_root.isNotEmpty) SelectableText('Authenticated root: $_root'),
        Text(
          'Current epoch: ${widget.keyEpoch}. Required next epoch: ${widget.keyEpoch + 1}.',
        ),
        const Text(
          'Removal is terminal. Future access is blocked only after rotation completes. Previously held data cannot be erased remotely. A remaining authorized device is required for recovery.',
        ),
        if (_status == 'RECOVERY_PENDING')
          const Text(
            'Resolve the authenticated-history conflict before removing a device.',
          ),
        CheckboxListTile(
          contentPadding: EdgeInsets.zero,
          title: const Text('Confirm removal of this exact device'),
          value: _confirmed,
          onChanged: _busy || _status != 'ACTIVE'
              ? null
              : (value) => setState(() {
                  _confirmed = value == true;
                }),
        ),
        FilledButton.icon(
          onPressed: _confirmed && !_busy && _status == 'ACTIVE'
              ? _remove
              : null,
          icon: const Icon(Icons.person_remove),
          label: Text(_busy ? 'Authorizing' : 'Remove Device'),
        ),
      ],
    ),
  );
}
