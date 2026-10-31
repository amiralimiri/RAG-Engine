# Implementation Tickets: Internalize Runtime Hooks into WorkflowRunner

- **Specification Reference:** [`docs/specs/0012-internalize-runtime-hooks-into-workflow-runner-spec.md`](../../docs/specs/0012-internalize-runtime-hooks-into-workflow-runner-spec.md)
- **Tickets Summary:** [`docs/specs/0012-internalize-runtime-hooks-into-workflow-runner-tickets.md`](../../docs/specs/0012-internalize-runtime-hooks-into-workflow-runner-tickets.md)
- **ADR Reference:** [`docs/adr/0012-internalize-runtime-hooks-into-workflow-runner.md`](../../docs/adr/0012-internalize-runtime-hooks-into-workflow-runner.md)

## Active Frontier

- [x] [01: WorkflowRunner Constructor Auto-Wiring, Interface Shrinkage & Singleton Reset](./01-runner-constructor-and-reset.md) (Completed)
- [ ] [02: Transport Layer Simplification (`app/api/routes.py`)](./02-routes-simplification.md) (Unblocked - ready to start)
- [ ] [03: Telemetry, Contexts, and Experiment Caller Migration](./03-telemetry-and-callers-migration.md) (Unblocked - ready to start)

## Dependency Pipeline

1. **01-runner-constructor-and-reset.md** &rarr; Core `WorkflowRunner` constructor defaults, interface shrinking, and conftest reset fixture.
2. **02-routes-simplification.md** &rarr; Delete `_runner_hooks()` from `app/api/routes.py` and streamline endpoints to 1-liners.
3. **03-telemetry-and-callers-migration.md** &rarr; Update `run_experiment.py` and telemetry test suites to use `_execute_with_state` / `execute`.
4. **04-runner-tests-and-conformance.md** &rarr; Modernize `test_workflow_runner.py` around constructor injection, test invariants, and run full test regression.
