import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../services/providers.dart';

/// Task Composer — enter a task prompt and submit it.
class TaskComposerScreen extends ConsumerStatefulWidget {
  final String nodeName;
  final String vmTitle;

  const TaskComposerScreen({
    super.key,
    required this.nodeName,
    required this.vmTitle,
  });

  @override
  ConsumerState<TaskComposerScreen> createState() =>
      _TaskComposerScreenState();
}

class _TaskComposerScreenState extends ConsumerState<TaskComposerScreen> {
  final _taskCtrl = TextEditingController();
  double _maxActions = 50;
  double _actionDelay = 1.0;
  bool _submitting = false;

  Future<void> _submit() async {
    final text = _taskCtrl.text.trim();
    if (text.isEmpty) return;

    setState(() => _submitting = true);

    try {
      final api = ref.read(apiClientProvider);
      if (api == null) return;

      final result = await api.submitTask(
        nodeName: widget.nodeName,
        vmTitle: widget.vmTitle,
        task: text,
        maxActions: _maxActions.round(),
        actionDelay: _actionDelay,
      );

      final taskId = result['task_id'] as String?;
      if (taskId != null && mounted) {
        context.go('/task/$taskId/live');
      }
    } catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text('Failed: $e')),
        );
      }
    } finally {
      setState(() => _submitting = false);
    }
  }

  @override
  void dispose() {
    _taskCtrl.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('New Task')),
      body: SingleChildScrollView(
        padding: const EdgeInsets.all(20),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            // Target info
            Card(
              child: Padding(
                padding: const EdgeInsets.all(14),
                child: Row(
                  children: [
                    const Icon(Icons.desktop_windows, size: 28),
                    const SizedBox(width: 12),
                    Expanded(
                      child: Column(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          Text(widget.nodeName,
                              style: const TextStyle(
                                  fontWeight: FontWeight.w600, fontSize: 15)),
                          Text(widget.vmTitle,
                              style: TextStyle(
                                  color: Colors.grey.shade600, fontSize: 13)),
                        ],
                      ),
                    ),
                  ],
                ),
              ),
            ),
            const SizedBox(height: 20),

            // Task prompt
            TextField(
              controller: _taskCtrl,
              decoration: const InputDecoration(
                labelText: 'Task',
                hintText: 'e.g. Open Notepad and type "Hello World"',
                border: OutlineInputBorder(),
                alignLabelWithHint: true,
              ),
              maxLines: 4,
              textInputAction: TextInputAction.done,
            ),
            const SizedBox(height: 24),

            // Max actions slider
            Text('Max actions: ${_maxActions.round()}'),
            Slider(
              value: _maxActions,
              min: 5,
              max: 100,
              divisions: 19,
              label: _maxActions.round().toString(),
              onChanged: (v) => setState(() => _maxActions = v),
            ),
            const SizedBox(height: 8),

            // Action delay slider
            Text('Action delay: ${_actionDelay.toStringAsFixed(1)}s'),
            Slider(
              value: _actionDelay,
              min: 0.5,
              max: 5.0,
              divisions: 9,
              label: '${_actionDelay.toStringAsFixed(1)}s',
              onChanged: (v) => setState(() => _actionDelay = v),
            ),
            const SizedBox(height: 24),

            // Submit
            FilledButton.icon(
              onPressed: _submitting ? null : _submit,
              icon: _submitting
                  ? const SizedBox(
                      width: 18,
                      height: 18,
                      child: CircularProgressIndicator(strokeWidth: 2),
                    )
                  : const Icon(Icons.play_arrow),
              label: Text(_submitting ? 'Starting...' : 'Start Task'),
            ),
          ],
        ),
      ),
    );
  }
}
