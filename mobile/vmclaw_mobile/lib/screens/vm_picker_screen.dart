import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../models/models.dart';
import '../services/providers.dart';

/// VM Picker — lists VMs on a specific node.
class VmPickerScreen extends ConsumerWidget {
  final String nodeName;
  const VmPickerScreen({super.key, required this.nodeName});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final vmsAsync = ref.watch(nodeVmsProvider(nodeName));

    return Scaffold(
      appBar: AppBar(title: Text('$nodeName — VMs')),
      body: vmsAsync.when(
        loading: () => const Center(child: CircularProgressIndicator()),
        error: (err, _) => Center(
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              Text('Error: $err'),
              const SizedBox(height: 12),
              FilledButton(
                onPressed: () => ref.invalidate(nodeVmsProvider(nodeName)),
                child: const Text('Retry'),
              ),
            ],
          ),
        ),
        data: (vms) {
          if (vms.isEmpty) {
            return const Center(child: Text('No VMs found on this node'));
          }
          return RefreshIndicator(
            onRefresh: () async =>
                ref.invalidate(nodeVmsProvider(nodeName)),
            child: ListView.builder(
              padding: const EdgeInsets.all(12),
              itemCount: vms.length,
              itemBuilder: (context, i) => _VmTile(
                vm: vms[i],
                nodeName: nodeName,
              ),
            ),
          );
        },
      ),
    );
  }
}

class _VmTile extends StatelessWidget {
  final VmInfo vm;
  final String nodeName;
  const _VmTile({required this.vm, required this.nodeName});

  @override
  Widget build(BuildContext context) {
    return Card(
      margin: const EdgeInsets.only(bottom: 8),
      child: ListTile(
        leading: const CircleAvatar(
          child: Icon(Icons.desktop_windows),
        ),
        title: Text(vm.title, style: const TextStyle(fontWeight: FontWeight.w500)),
        trailing: const Icon(Icons.play_arrow),
        onTap: () {
          context.push(
            '/task/new',
            extra: {'node_name': nodeName, 'vm_title': vm.title},
          );
        },
      ),
    );
  }
}
