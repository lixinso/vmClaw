/// Riverpod providers for app-wide state.
library;

import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../models/models.dart';
import '../services/api_client.dart';
import '../services/settings_store.dart';

// ----- Settings -----

final settingsStoreProvider = Provider((_) => SettingsStore());

final connectionSettingsProvider =
    StateProvider<ConnectionSettings?>((ref) => null);

// ----- API Client -----

final apiClientProvider = Provider<ApiClient?>((ref) {
  final settings = ref.watch(connectionSettingsProvider);
  if (settings == null) return null;
  return ApiClient(settings);
});

// ----- Nodes -----

final nodesProvider = FutureProvider<List<NodeInfo>>((ref) async {
  final api = ref.watch(apiClientProvider);
  if (api == null) return [];
  return api.listNodes();
});

// ----- VMs for a node -----

final nodeVmsProvider =
    FutureProvider.family<List<VmInfo>, String>((ref, nodeName) async {
  final api = ref.watch(apiClientProvider);
  if (api == null) return [];
  return api.listNodeVms(nodeName);
});

// ----- Task history -----

final taskHistoryProvider = FutureProvider<List<TaskInfo>>((ref) async {
  final api = ref.watch(apiClientProvider);
  if (api == null) return [];
  return api.listTasks();
});
