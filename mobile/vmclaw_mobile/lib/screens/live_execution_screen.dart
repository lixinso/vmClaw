import 'dart:convert';
import 'dart:typed_data';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import 'package:web_socket_channel/web_socket_channel.dart';

import '../models/models.dart';
import '../services/providers.dart';

/// Live Execution screen — WebSocket-driven task viewer with controls.
class LiveExecutionScreen extends ConsumerStatefulWidget {
  final String taskId;
  const LiveExecutionScreen({super.key, required this.taskId});

  @override
  ConsumerState<LiveExecutionScreen> createState() =>
      _LiveExecutionScreenState();
}

class _LiveExecutionScreenState extends ConsumerState<LiveExecutionScreen> {
  WebSocketChannel? _channel;
  Uint8List? _screenshot;
  final List<String> _log = [];
  int _step = 0;
  String _status = 'running';
  bool _paused = false;
  Map<String, dynamic>? _pendingApproval;
  final _scrollCtrl = ScrollController();

  @override
  void initState() {
    super.initState();
    _connectWs();
  }

  void _connectWs() {
    final api = ref.read(apiClientProvider);
    if (api == null) return;

    final url = api.wsUrl(widget.taskId);
    _channel = WebSocketChannel.connect(Uri.parse(url));
    _channel!.stream.listen(
      _onMessage,
      onDone: () {
        if (mounted && _status == 'running') {
          setState(() => _status = 'disconnected');
        }
      },
      onError: (e) {
        if (mounted) {
          _addLog('[WS error] $e');
        }
      },
    );
  }

  void _onMessage(dynamic raw) {
    final event = TaskEvent.fromJson(
      jsonDecode(raw as String) as Map<String, dynamic>,
    );

    setState(() {
      if (event.isScreenshot && event.data is String) {
        _screenshot = base64Decode(event.data as String);
      } else if (event.isStep) {
        _step = (event.data is int) ? event.data as int : 0;
      } else if (event.isAction && event.data is Map) {
        final a = event.data as Map;
        final action = a['action'] ?? '?';
        final reason = a['reason'] ?? '';
        _addLog('[Step $_step] $action — $reason');
      } else if (event.isLog) {
        _addLog(event.data?.toString() ?? '');
      } else if (event.isPaused) {
        _paused = true;
        _addLog('⏸ Task paused');
      } else if (event.isResumed) {
        _paused = false;
        _addLog('▶ Task resumed');
      } else if (event.isApprovalRequired && event.data is Map) {
        _pendingApproval = Map<String, dynamic>.from(event.data as Map);
        _addLog('⚠ Approval required');
      } else if (event.isDone) {
        _status = event.data?.toString() ?? 'done';
        _addLog('✓ Task finished: $_status');
      }
    });
  }

  void _addLog(String msg) {
    _log.add(msg);
    // Auto-scroll to bottom
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (_scrollCtrl.hasClients) {
        _scrollCtrl.animateTo(
          _scrollCtrl.position.maxScrollExtent,
          duration: const Duration(milliseconds: 150),
          curve: Curves.easeOut,
        );
      }
    });
  }

  Future<void> _pause() async {
    try {
      await ref.read(apiClientProvider)?.pauseTask(widget.taskId);
    } catch (e) {
      _addLog('Pause failed: $e');
    }
  }

  Future<void> _resume() async {
    try {
      await ref.read(apiClientProvider)?.resumeTask(widget.taskId);
    } catch (e) {
      _addLog('Resume failed: $e');
    }
  }

  Future<void> _stop() async {
    try {
      await ref.read(apiClientProvider)?.cancelTask(widget.taskId);
      setState(() => _status = 'stopped');
    } catch (e) {
      _addLog('Stop failed: $e');
    }
  }

  Future<void> _approve(bool approved) async {
    try {
      await ref
          .read(apiClientProvider)
          ?.approveAction(widget.taskId, approved: approved);
      setState(() => _pendingApproval = null);
    } catch (e) {
      _addLog('Approve failed: $e');
    }
  }

  void _onScreenshotTap(TapDownDetails details, BoxConstraints constraints) {
    if (_status != 'running' || _screenshot == null) return;

    // Scale tap position to the screenshot's original coordinate space
    // The server resizes to max 720px wide; we send raw pixel coords and let
    // the orchestrator + executor map to the real VM resolution.
    final rx = details.localPosition.dx / constraints.maxWidth;
    final ry = details.localPosition.dy / constraints.maxHeight;
    // The mobile screenshot is max 720px wide. Map to that space.
    final x = (rx * 720).round();
    final y = (ry * 720 / (constraints.maxWidth / constraints.maxHeight))
        .round();

    ref.read(apiClientProvider)?.guideClick(widget.taskId, x, y);
    _addLog('👆 Guide click at ($x, $y)');
  }

  @override
  void dispose() {
    _channel?.sink.close();
    _scrollCtrl.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final isRunning = _status == 'running';

    return Scaffold(
      appBar: AppBar(
        title: Text('Task ${widget.taskId}'),
        actions: [
          // Step counter
          Padding(
            padding: const EdgeInsets.symmetric(horizontal: 8),
            child: Center(
              child: Text('Step $_step',
                  style: const TextStyle(fontWeight: FontWeight.w600)),
            ),
          ),
          // Status badge
          Padding(
            padding: const EdgeInsets.only(right: 12),
            child: Center(child: _StatusBadge(status: _status)),
          ),
        ],
      ),
      body: Column(
        children: [
          // Screenshot area
          Expanded(
            flex: 3,
            child: Container(
              color: Colors.black,
              width: double.infinity,
              child: _screenshot != null
                  ? LayoutBuilder(
                      builder: (context, constraints) {
                        return GestureDetector(
                          onTapDown: isRunning
                              ? (d) => _onScreenshotTap(d, constraints)
                              : null,
                          child: Image.memory(
                            _screenshot!,
                            fit: BoxFit.contain,
                            gaplessPlayback: true,
                          ),
                        );
                      },
                    )
                  : const Center(
                      child: Column(
                        mainAxisSize: MainAxisSize.min,
                        children: [
                          CircularProgressIndicator(color: Colors.white54),
                          SizedBox(height: 12),
                          Text('Waiting for screenshot...',
                              style: TextStyle(color: Colors.white54)),
                        ],
                      ),
                    ),
            ),
          ),

          // Approval banner
          if (_pendingApproval != null)
            MaterialBanner(
              content: Text(
                'Approve action: ${_pendingApproval!['action']} — '
                '${_pendingApproval!['reason'] ?? ''}',
              ),
              leading: const Icon(Icons.warning_amber, color: Colors.orange),
              actions: [
                TextButton(
                  onPressed: () => _approve(false),
                  child: const Text('Reject'),
                ),
                FilledButton(
                  onPressed: () => _approve(true),
                  child: const Text('Approve'),
                ),
              ],
            ),

          // Action log
          Expanded(
            flex: 2,
            child: Container(
              color: Colors.grey.shade100,
              child: ListView.builder(
                controller: _scrollCtrl,
                padding: const EdgeInsets.all(8),
                itemCount: _log.length,
                itemBuilder: (context, i) => Padding(
                  padding: const EdgeInsets.only(bottom: 2),
                  child: Text(
                    _log[i],
                    style:
                        const TextStyle(fontSize: 12, fontFamily: 'monospace'),
                  ),
                ),
              ),
            ),
          ),

          // Controls
          SafeArea(
            child: Padding(
              padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 8),
              child: Row(
                children: [
                  if (isRunning && !_paused)
                    Expanded(
                      child: OutlinedButton.icon(
                        onPressed: _pause,
                        icon: const Icon(Icons.pause),
                        label: const Text('Pause'),
                      ),
                    ),
                  if (isRunning && _paused)
                    Expanded(
                      child: FilledButton.icon(
                        onPressed: _resume,
                        icon: const Icon(Icons.play_arrow),
                        label: const Text('Resume'),
                      ),
                    ),
                  if (isRunning) const SizedBox(width: 12),
                  if (isRunning)
                    Expanded(
                      child: OutlinedButton.icon(
                        onPressed: _stop,
                        icon:
                            const Icon(Icons.stop, color: Colors.red),
                        label: const Text('Stop',
                            style: TextStyle(color: Colors.red)),
                      ),
                    ),
                  if (!isRunning)
                    Expanded(
                      child: FilledButton.icon(
                        onPressed: () => context.go('/dashboard'),
                        icon: const Icon(Icons.arrow_back),
                        label: const Text('Back to Dashboard'),
                      ),
                    ),
                ],
              ),
            ),
          ),
        ],
      ),
    );
  }
}

class _StatusBadge extends StatelessWidget {
  final String status;
  const _StatusBadge({required this.status});

  @override
  Widget build(BuildContext context) {
    final (color, icon) = switch (status) {
      'running' => (Colors.blue, Icons.play_arrow),
      'done' => (Colors.green, Icons.check_circle),
      'error' => (Colors.red, Icons.error),
      'stopped' => (Colors.orange, Icons.stop_circle),
      _ => (Colors.grey, Icons.help_outline),
    };

    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 3),
      decoration: BoxDecoration(
        color: color.withValues(alpha: 0.12),
        borderRadius: BorderRadius.circular(12),
      ),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          Icon(icon, size: 14, color: color),
          const SizedBox(width: 4),
          Text(status,
              style: TextStyle(
                  fontSize: 12, color: color, fontWeight: FontWeight.w600)),
        ],
      ),
    );
  }
}
