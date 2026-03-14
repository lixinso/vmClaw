import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../models/models.dart';
import '../services/providers.dart';

/// Fleet Dashboard — shows all nodes with status, VM count, running tasks.
class DashboardScreen extends ConsumerWidget {
  const DashboardScreen({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final nodesAsync = ref.watch(nodesProvider);

    return Scaffold(
      appBar: AppBar(
        title: const Text('Fleet Dashboard'),
        actions: [
          IconButton(
            icon: const Icon(Icons.history),
            tooltip: 'Task History',
            onPressed: () => context.push('/history'),
          ),
          IconButton(
            icon: const Icon(Icons.settings),
            tooltip: 'Settings',
            onPressed: () => context.go('/'),
          ),
        ],
      ),
      body: nodesAsync.when(
        loading: () => const Center(child: CircularProgressIndicator()),
        error: (err, _) => Center(
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              const Icon(Icons.error_outline, size: 48, color: Colors.red),
              const SizedBox(height: 12),
              Text('Failed to load nodes\n$err',
                  textAlign: TextAlign.center),
              const SizedBox(height: 16),
              FilledButton(
                onPressed: () => ref.invalidate(nodesProvider),
                child: const Text('Retry'),
              ),
            ],
          ),
        ),
        data: (nodes) {
          if (nodes.isEmpty) {
            return const Center(child: Text('No nodes found'));
          }
          return RefreshIndicator(
            onRefresh: () async => ref.invalidate(nodesProvider),
            child: ListView.builder(
              padding: const EdgeInsets.all(12),
              itemCount: nodes.length,
              itemBuilder: (context, i) => _NodeCard(node: nodes[i]),
            ),
          );
        },
      ),
    );
  }
}

class _NodeCard extends StatelessWidget {
  final NodeInfo node;
  const _NodeCard({required this.node});

  @override
  Widget build(BuildContext context) {
    final isOnline = node.isOnline;
    final statusColor = isOnline ? Colors.green : Colors.red;

    return Card(
      margin: const EdgeInsets.only(bottom: 10),
      child: ListTile(
        leading: CircleAvatar(
          backgroundColor: statusColor.withValues(alpha: 0.15),
          child: Icon(
            isOnline ? Icons.computer : Icons.computer_outlined,
            color: statusColor,
          ),
        ),
        title: Row(
          children: [
            Text(node.nodeName,
                style: const TextStyle(fontWeight: FontWeight.w600)),
            const SizedBox(width: 8),
            if (node.isSelf)
              Container(
                padding:
                    const EdgeInsets.symmetric(horizontal: 6, vertical: 1),
                decoration: BoxDecoration(
                  color: Colors.blue.withValues(alpha: 0.12),
                  borderRadius: BorderRadius.circular(4),
                ),
                child: const Text('self',
                    style: TextStyle(fontSize: 11, color: Colors.blue)),
              ),
          ],
        ),
        subtitle: Text(
          '${node.role ?? "?"} · '
          '${node.vmCount} VM${node.vmCount == 1 ? "" : "s"}'
          '${node.runningTasks > 0 ? " · ${node.runningTasks} running" : ""}',
        ),
        trailing: Icon(Icons.chevron_right,
            color: isOnline ? null : Colors.grey.shade400),
        enabled: isOnline,
        onTap: isOnline
            ? () => context.push('/nodes/${node.nodeName}/vms')
            : null,
      ),
    );
  }
}
