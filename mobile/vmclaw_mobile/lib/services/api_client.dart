/// HTTP API client for the vmClaw gateway.
library;

import 'dart:typed_data';

import 'package:dio/dio.dart';

import '../models/models.dart';

class ApiClient {
  late final Dio _dio;
  ConnectionSettings _settings;

  ApiClient(this._settings) {
    _dio = Dio(BaseOptions(
      baseUrl: _settings.gatewayUrl,
      connectTimeout: const Duration(seconds: 10),
      receiveTimeout: const Duration(seconds: 60),
      headers: _authHeaders(),
    ));
  }

  Map<String, String> _authHeaders() {
    final token = _settings.token;
    if (token.isNotEmpty) {
      return {'Authorization': 'Bearer $token'};
    }
    return {};
  }

  void updateSettings(ConnectionSettings settings) {
    _settings = settings;
    _dio.options.baseUrl = settings.gatewayUrl;
    _dio.options.headers = _authHeaders();
  }

  // ----- Connection test -----

  Future<Map<String, dynamic>> getGatewayInfo() async {
    final resp = await _dio.get('/api/mobile/info');
    return resp.data as Map<String, dynamic>;
  }

  // ----- Nodes -----

  Future<List<NodeInfo>> listNodes() async {
    final resp = await _dio.get('/api/mobile/nodes');
    final list = resp.data as List;
    return list
        .map((e) => NodeInfo.fromJson(e as Map<String, dynamic>))
        .toList();
  }

  // ----- VMs -----

  Future<List<VmInfo>> listNodeVms(String nodeName) async {
    final resp = await _dio.get('/api/mobile/nodes/$nodeName/vms');
    final list = resp.data as List;
    return list
        .map((e) => VmInfo.fromJson(e as Map<String, dynamic>))
        .toList();
  }

  // ----- Tasks -----

  Future<List<TaskInfo>> listTasks({
    String? status,
    int limit = 50,
    int offset = 0,
  }) async {
    final resp = await _dio.get('/api/mobile/tasks', queryParameters: {
      if (status != null) 'status': status,
      'limit': limit,
      'offset': offset,
    });
    final list = resp.data as List;
    return list
        .map((e) => TaskInfo.fromJson(e as Map<String, dynamic>))
        .toList();
  }

  Future<TaskInfo> getTask(String taskId) async {
    final resp = await _dio.get('/api/mobile/tasks/$taskId');
    return TaskInfo.fromJson(resp.data as Map<String, dynamic>);
  }

  Future<Map<String, dynamic>> submitTask({
    required String nodeName,
    required String vmTitle,
    required String task,
    int maxActions = 50,
    double actionDelay = 1.0,
  }) async {
    final resp = await _dio.post('/api/mobile/tasks', data: {
      'node_name': nodeName,
      'vm_title': vmTitle,
      'task': task,
      'max_actions': maxActions,
      'action_delay': actionDelay,
    });
    return resp.data as Map<String, dynamic>;
  }

  Future<void> cancelTask(String taskId) async {
    await _dio.post('/api/mobile/tasks/$taskId/cancel');
  }

  Future<void> pauseTask(String taskId) async {
    await _dio.post('/api/mobile/tasks/$taskId/pause');
  }

  Future<void> resumeTask(String taskId) async {
    await _dio.post('/api/mobile/tasks/$taskId/resume');
  }

  Future<void> approveAction(String taskId, {bool approved = true}) async {
    await _dio.post('/api/mobile/tasks/$taskId/approve', data: {
      'approved': approved,
    });
  }

  Future<void> guideClick(String taskId, int x, int y) async {
    await _dio.post('/api/mobile/tasks/$taskId/guide-click', data: {
      'x': x,
      'y': y,
    });
  }

  Future<void> guideType(String taskId, String text) async {
    await _dio.post('/api/mobile/tasks/$taskId/guide-type', data: {
      'text': text,
    });
  }

  /// Fetch the latest screenshot as raw JPEG bytes.
  Future<Uint8List?> getScreenshot(String taskId) async {
    try {
      final resp = await _dio.get(
        '/api/mobile/tasks/$taskId/screenshot',
        options: Options(responseType: ResponseType.bytes),
      );
      return resp.data as Uint8List;
    } on DioException catch (e) {
      if (e.response?.statusCode == 404) return null;
      rethrow;
    }
  }

  /// Build the WebSocket URL for a task stream.
  String wsUrl(String taskId) {
    var base = _settings.gatewayUrl
        .replaceFirst('https://', 'wss://')
        .replaceFirst('http://', 'ws://');
    // Remove trailing slash
    if (base.endsWith('/')) base = base.substring(0, base.length - 1);
    final token = _settings.token;
    return '$base/ws/mobile/tasks/$taskId?token=$token';
  }
}
