import 'package:go_router/go_router.dart';

import '../screens/connect_screen.dart';
import '../screens/dashboard_screen.dart';
import '../screens/live_execution_screen.dart';
import '../screens/task_composer_screen.dart';
import '../screens/task_history_screen.dart';
import '../screens/vm_picker_screen.dart';

final router = GoRouter(
  initialLocation: '/',
  routes: [
    GoRoute(
      path: '/',
      builder: (context, state) => const ConnectScreen(),
    ),
    GoRoute(
      path: '/dashboard',
      builder: (context, state) => const DashboardScreen(),
    ),
    GoRoute(
      path: '/nodes/:nodeName/vms',
      builder: (context, state) {
        final nodeName = state.pathParameters['nodeName']!;
        return VmPickerScreen(nodeName: nodeName);
      },
    ),
    GoRoute(
      path: '/task/new',
      builder: (context, state) {
        final extra = state.extra as Map<String, String>? ?? {};
        return TaskComposerScreen(
          nodeName: extra['node_name'] ?? '',
          vmTitle: extra['vm_title'] ?? '',
        );
      },
    ),
    GoRoute(
      path: '/task/:taskId/live',
      builder: (context, state) {
        final taskId = state.pathParameters['taskId']!;
        return LiveExecutionScreen(taskId: taskId);
      },
    ),
    GoRoute(
      path: '/history',
      builder: (context, state) => const TaskHistoryScreen(),
    ),
  ],
);
