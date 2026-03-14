/// Data models matching the vmClaw server API responses.
library;

/// A node in the fleet.
class NodeInfo {
  final String nodeName;
  final String? role;
  final String status; // "online" / "offline"
  final bool isSelf;
  final int vmCount;
  final int runningTasks;

  const NodeInfo({
    required this.nodeName,
    this.role,
    required this.status,
    this.isSelf = false,
    this.vmCount = 0,
    this.runningTasks = 0,
  });

  factory NodeInfo.fromJson(Map<String, dynamic> json) => NodeInfo(
        nodeName: json['node_name'] as String? ?? 'unknown',
        role: json['role'] as String?,
        status: json['status'] as String? ?? 'unknown',
        isSelf: json['is_self'] as bool? ?? false,
        vmCount: json['vm_count'] as int? ?? 0,
        runningTasks: json['running_tasks'] as int? ?? 0,
      );

  bool get isOnline => status == 'online';
}

/// A VM on a node.
class VmInfo {
  final String title;
  final int? hwnd;

  const VmInfo({required this.title, this.hwnd});

  factory VmInfo.fromJson(Map<String, dynamic> json) => VmInfo(
        title: json['title'] as String? ?? '?',
        hwnd: json['hwnd'] as int?,
      );
}

/// A task record (from history or live).
class TaskInfo {
  final String taskId;
  final String nodeName;
  final String vmTitle;
  final String taskText;
  final String status;
  final String? outcome;
  final int actionsTaken;
  final String? createdAt;
  final String? endedAt;

  const TaskInfo({
    required this.taskId,
    required this.nodeName,
    required this.vmTitle,
    required this.taskText,
    required this.status,
    this.outcome,
    this.actionsTaken = 0,
    this.createdAt,
    this.endedAt,
  });

  factory TaskInfo.fromJson(Map<String, dynamic> json) => TaskInfo(
        taskId: json['task_id'] as String? ?? '',
        nodeName: json['node_name'] as String? ?? '',
        vmTitle: json['vm_title'] as String? ?? '',
        taskText: json['task_text'] as String? ?? '',
        status: json['status'] as String? ?? 'unknown',
        outcome: json['outcome'] as String?,
        actionsTaken: json['actions_taken'] as int? ?? 0,
        createdAt: json['created_at'] as String?,
        endedAt: json['ended_at'] as String?,
      );

  bool get isRunning => status == 'running';
  bool get isDone => status == 'done';
  bool get isError => status == 'error';
}

/// A task event received over WebSocket.
class TaskEvent {
  final String type;
  final dynamic data;

  const TaskEvent({required this.type, this.data});

  factory TaskEvent.fromJson(Map<String, dynamic> json) => TaskEvent(
        type: json['type'] as String? ?? '',
        data: json['data'],
      );

  bool get isScreenshot => type == 'screenshot';
  bool get isAction => type == 'action';
  bool get isLog => type == 'log';
  bool get isDone => type == 'done';
  bool get isPaused => type == 'paused';
  bool get isResumed => type == 'resumed';
  bool get isApprovalRequired => type == 'approval_required';
  bool get isStep => type == 'step';
  bool get isTokens => type == 'tokens';
}

/// Connection settings stored in secure storage.
class ConnectionSettings {
  final String gatewayUrl;
  final String token;

  const ConnectionSettings({
    required this.gatewayUrl,
    required this.token,
  });

  ConnectionSettings copyWith({String? gatewayUrl, String? token}) =>
      ConnectionSettings(
        gatewayUrl: gatewayUrl ?? this.gatewayUrl,
        token: token ?? this.token,
      );
}
