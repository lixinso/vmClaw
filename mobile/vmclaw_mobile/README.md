# vmClaw Mobile

Flutter mobile client for vmClaw — control your AI workforce from your pocket.

## Setup

1. Install [Flutter SDK](https://docs.flutter.dev/get-started/install) (3.2+)
2. From this directory:

```bash
cd mobile/vmclaw_mobile
flutter pub get
flutter run
```

## Screens

| Screen | Route | Description |
|--------|-------|-------------|
| Connect | `/` | Enter gateway URL + Bearer token |
| Dashboard | `/dashboard` | Fleet overview — nodes with status |
| VM Picker | `/nodes/{node}/vms` | VMs on a selected node |
| Task Composer | `/task/new` | Enter task prompt, submit |
| Live Execution | `/task/{id}/live` | Screenshot + action log + controls |
| Task History | `/history` | Past task executions |

## Architecture

```
lib/
  main.dart              — App entry point
  models/
    models.dart          — Data classes (NodeInfo, VmInfo, TaskInfo, TaskEvent)
  services/
    api_client.dart      — HTTP + WS client for /api/mobile/* endpoints
    settings_store.dart  — Secure storage for connection settings
    providers.dart       — Riverpod state providers
    router.dart          — GoRouter navigation config
  screens/
    connect_screen.dart      — Gateway connection
    dashboard_screen.dart    — Fleet overview
    vm_picker_screen.dart    — VM selection
    task_composer_screen.dart — Task creation
    live_execution_screen.dart — Live task view + controls
    task_history_screen.dart — Past tasks
```

## Backend Requirements

The app connects to a vmClaw node with `gateway_enabled = true` in its fleet config.
All communication uses the same Bearer token as fleet peer authentication.
