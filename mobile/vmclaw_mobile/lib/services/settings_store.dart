/// Secure storage wrapper for connection settings.
library;

import 'package:flutter_secure_storage/flutter_secure_storage.dart';

import '../models/models.dart';

const _keyUrl = 'vmclaw_gateway_url';
const _keyToken = 'vmclaw_gateway_token';

class SettingsStore {
  final _storage = const FlutterSecureStorage();

  Future<ConnectionSettings?> load() async {
    final url = await _storage.read(key: _keyUrl);
    final token = await _storage.read(key: _keyToken);
    if (url == null || url.isEmpty) return null;
    return ConnectionSettings(gatewayUrl: url, token: token ?? '');
  }

  Future<void> save(ConnectionSettings settings) async {
    await _storage.write(key: _keyUrl, value: settings.gatewayUrl);
    await _storage.write(key: _keyToken, value: settings.token);
  }

  Future<void> clear() async {
    await _storage.delete(key: _keyUrl);
    await _storage.delete(key: _keyToken);
  }
}
