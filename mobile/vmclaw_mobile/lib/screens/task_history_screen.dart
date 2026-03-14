import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../models/models.dart';
import '../services/providers.dart';

/// Task History — paginated list of past task executions.
class TaskHistoryScreen extends ConsumerWidget {
  const TaskHistoryScreen({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final tasksAsync = ref.watch(taskHistoryProvider);

    return Scaffold(
      appBar: AppBar(title: const Text('Task History')),
      body: tasksAsync.when(
        loading: () => const Center(child: CircularProgressIndicator()),
        error: (err, _) => Center(
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              Text('Error: $err'),
              const SizedBox(height: 12),
              FilledButton(
                onPressed: () => ref.invalidate(taskHistoryProvider),
                child: const Text('Retry'),
              ),
            ],
          ),
        ),
        data: (tasks) {
          if (tasks.isEmpty) {
            return const Center(
              child: Column(
                mainAxisSize: MainAxisSize.min,
                children: [
                  Icon(Icons.inbox_outlined, size: 48, color: Colors.grey),
                  SizedBox(height: 12),
                  Text('No tasks yet', style: TextStyle(color: Colors.grey)),
                ],
              ),
            );
          }
          return RefreshIndicator(
            onRefresh: () async => ref.invalidate(taskHistoryProvider),
            child: ListView.builder(
              padding: const EdgeInsets.all(12),
              itemCount: tasks.length,
              itemBuilder: (context, i) => _TaskTile(task: tasks[i]),
            ),
          );
        },
      ),
    );
  }
}

class _TaskTile extends StatelessWidget {
  final TaskInfo task;
  const _TaskTile({required this.task});

  @override
  Widget build(BuildContext context) {
    final (color, icon) = switch (task.status) {
      'running' => (Colors.blue, Icons.play_arrow),
      'done' => (Colors.green, Icons.check_circle),
      'error' => (Colors.red, Icons.error),
      'stopped' => (Colors.orange, Icons.stop_circle),
      'interrupted' => (Colors.amber, Icons.warning),
      'max_actions' => (Colors.purple, Icons.timer_off),
      _ => (Colors.grey, Icons.help_outline),
    };

    return Card(
      margin: const EdgeInsets.only(bottom: 8),
      child: ListTile(
        leading: CircleAvatar(
          backgroundColor: color.withValues(alpha: 0.12),
          child: Icon(icon, color: color, size: 20),
        ),
        title: Text(
          task.taskText,
          maxLines: 1,
          overflow: TextOverflow.ellipsis,
          style: const TextStyle(fontWeight: FontWeight.w500),
        ),
        subtitle: Text(
          '${task.nodeName} / ${task.vmTitle} · '
          '${task.actionsTaken} actions · ${task.status}',
          style: const TextStyle(fontSize: 12),
        ),
        trailing: task.isRunning
            ? const Icon(Icons.chevron_right)
            : const Icon(Icons.replay, size: 20),
        onTap: () {
          if (task.isRunning) {
            context.push('/task/${task.taskId}/live');
          }
          // Future: re-run or detail view for completed tasks
        },
      ),
    );
  }
}
