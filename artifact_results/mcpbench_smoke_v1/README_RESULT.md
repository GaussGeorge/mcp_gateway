# MCP-Bench Small Workflow-Shape Smoke Evidence

This artifact provides MCP-Bench-derived workflow-shape smoke evidence for PlanGate in a single-machine controlled backend setup.
It uses task metadata and dependency structure as workflow-shape input and does not require full MCP-Bench server deployment.

## Source Metadata

- mcpbench_tasks_multi_2server_runner_format.json: 8a4e038637c3e4c56482aedc8e57b09c5517056f5c76685eacef42183a54fd93
- mcpbench_tasks_multi_3server_runner_format.json: 8b92f3d57d05f006dae9ed0e2e88d3713f9fc4d56d532178772fbf584db1c235
- mcpbench_tasks_single_runner_format.json: 07b8f8eca337110052a7bb645f46f136953b6f6f8a96928771cbed483406de8b
- selected task count: 30
- task_set_sha256: a02b549c93adb060dd08a005eeab57c1a57740849803264f93d613661f720311
- backend_mode: mock_sterile_single_machine

## Selected Task Sample

- biomcp_001 | single_server | est_steps=7 | src=mcpbench_tasks_single_runner_format.json
- context7_000 | single_server | est_steps=7 | src=mcpbench_tasks_single_runner_format.json
- dex_paprika_001 | single_server | est_steps=7 | src=mcpbench_tasks_single_runner_format.json
- fruityvice_000 | single_server | est_steps=5 | src=mcpbench_tasks_single_runner_format.json
- game_trends_001 | single_server | est_steps=7 | src=mcpbench_tasks_single_runner_format.json
- google_maps_weather_data_000 | two_server_combinations | est_steps=9 | src=mcpbench_tasks_multi_2server_runner_format.json
- google_maps_weather_data_national_parks_000 | three_server_combinations | est_steps=11 | src=mcpbench_tasks_multi_3server_runner_format.json
- google_maps_weather_data_national_parks_001 | three_server_combinations | est_steps=11 | src=mcpbench_tasks_multi_3server_runner_format.json
- hugging_face_paper_search_001 | two_server_combinations | est_steps=9 | src=mcpbench_tasks_multi_2server_runner_format.json
- hugging_face_paper_search_wikipedia_001 | three_server_combinations | est_steps=11 | src=mcpbench_tasks_multi_3server_runner_format.json
- medical_calculator_000 | single_server | est_steps=7 | src=mcpbench_tasks_single_runner_format.json
- medical_calculator_fruityvice_biomcp_000 | three_server_combinations | est_steps=11 | src=mcpbench_tasks_multi_3server_runner_format.json
- medical_calculator_fruityvice_biomcp_001 | three_server_combinations | est_steps=11 | src=mcpbench_tasks_multi_3server_runner_format.json
- medical_calculator_wikipedia_fruityvice_001 | three_server_combinations | est_steps=5 | src=mcpbench_tasks_multi_3server_runner_format.json
- metropolitan_museum_huge_icons_wikipedia_001 | three_server_combinations | est_steps=11 | src=mcpbench_tasks_multi_3server_runner_format.json
- ... (15 more tasks)

## Claim Boundary

Allowed claim:
- PlanGate compatibility is smoke-tested against MCP-Bench-derived workflow-shape diversity under single-machine controlled backend conditions.

Not allowed:
- model accuracy or benchmark leaderboard claims
- production-readiness claim
- CloudLab/distributed-state claim
- full MCP-Bench end-to-end deployment equivalence claim
- universal policy dominance claim
