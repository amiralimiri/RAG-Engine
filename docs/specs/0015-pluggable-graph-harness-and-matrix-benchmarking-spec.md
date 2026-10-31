# Technical Specification: Pluggable Graph Harness and Matrix Benchmarking Engine

- **ADR Reference:** `docs/adr/0015-pluggable-graph-harness-and-matrix-benchmarking.md`
- **Target Version:** `0.15.0`
- **Status:** Approved / In Progress
- **Triage Label:** `ready-for-agent`
- **Work Packages / Tickets:** `docs/specs/0015-pluggable-graph-harness-and-matrix-benchmarking-tickets.md`

---

## 1. Problem Statement

1. **Transport Layer Reverse Coupling**:
   `WorkflowDefinition` in `app/rag/workflows/base.py` and concrete workflow classes (`SelfCorrectiveRagWorkflow`, `ReActAgentWorkflow`) directly reference `ChatRequest` and `ChatResponse` from `app.api.schemas`. This violates the architectural mandate in `docs/agents/workflow-execution.md` prohibiting domain coupling to transport schemas, and prevents headless CLI tools and external plugins from executing without web transport dependencies.
2. **Fixed Graph Packaging**:
   Every workflow in `rag-engine` is currently a bespoke Python class defined inside `app/rag/workflows/`. When an external studio visual designer or DSL compiler produces a LangGraph graph, there is no generic base class or dynamic loading convention to plug it in without modifying `rag-engine` source code.
3. **No Matrix Benchmarking**:
   `evaluation/run_experiment.py` only tests fixed workflow names against a benchmark dataset. It cannot evaluate a matrix of parameter configurations (e.g. comparing Prompt v1 vs Prompt v2, or GPT-4o vs Claude 3.5 Sonnet on the same workflow topology).
4. **Evaluator Seam Lacks Ground-Truth Metrics**:
   The `Evaluator` protocol seam (`app/rag/evaluation/base.py`) only evaluates reference-free RAG metrics (`faithfulness`, `answer_relevancy`). Golden benchmarks contain `expected_output` and `expected_source`, but these are ignored by the evaluation seam.
5. **Observability Trace Fragmentation**:
   Langfuse traces only receive `workflow:<name>` tags, obscuring which variant, prompt version, or model profile generated the run.

---

## 2. Solution Overview

Evolve `rag-engine` into an enterprise-grade execution, evaluation, and benchmarking harness:
1. **Domain DTOs**: Create `WorkflowInput` and `WorkflowOutput` in `app/rag/workflows/base.py`. Migrate `WorkflowDefinition` to use these DTOs. `WorkflowRunner` bridges `ChatRequest` $\leftrightarrow$ `WorkflowInput` and `WorkflowOutput` $\leftrightarrow$ `ChatResponse`.
2. **Standard State & Pluggable Base**: Define `StandardWorkflowState` (`TypedDict`) and `PluggableWorkflow` implementing `WorkflowDefinition` to wrap arbitrary compiled LangGraph graphs.
3. **Manifest + Factory Plugin Architecture**: Implement `WorkflowRegistry.load_from_directory(path)` to discover and load external workflow packages containing a `manifest.json` and a `get_graph(config)` factory.
4. **Execution Profiles & 3-Tier Precedence**: Implement `ExecutionProfile` and a 3-tier resolution hierarchy in `WorkflowRunner`, propagating parameters into LangGraph's `RunnableConfig["configurable"]` and multi-dimensional Langfuse tags.
5. **Supervised Metrics & Matrix Benchmarking**: Extend `Evaluator` with optional `expected_output` and `expected_source`, and update `evaluation/run_experiment.py` to support YAML-driven matrix experiments linked to Langfuse v5 Dataset Runs.

---

## 3. User Stories

1. **As an external graph designer**, I want to export a compiled LangGraph graph and `manifest.json` from the visual studio into `workflows/` and have `rag-engine` discover, register, and stream it via SSE without writing custom Python adapters.
2. **As an ML evaluation engineer**, I want to run `uv run python evaluation/run_experiment.py --matrix matrix.yaml` to benchmark Workflow A against Workflow B across multiple prompt versions and model backends, outputting comparative metric tables.
3. **As an observability engineer**, I want Langfuse traces and dataset runs to be tagged with `workflow:<id>`, `variant:<id>`, `model:<name>`, and `prompt:<ver>`, accompanied by ground-truth accuracy scores.
4. **As an application maintainer**, I want `app/rag/workflows/` to have zero imports from `app.api.schemas`, maintaining strict separation of concerns.

---

## 4. Key Contracts & Architecture

### 4.1 Domain DTOs (`app/rag/workflows/base.py`)
```python
class WorkflowInput(BaseModel):
    question: str
    messages: list[dict[str, Any]] | None = None
    attachments: list[dict[str, Any]] | None = None
    session_id: str | None = None
    user_id: str | None = None
    trace_id: str | None = None
    variant_id: str | None = None
    parameters: dict[str, Any] = Field(default_factory=dict)

class WorkflowOutput(BaseModel):
    workflow: str
    answer: str
    source_used: str = "direct"
    citations: list[dict[str, Any]] = Field(default_factory=list)
    trace: list[str] = Field(default_factory=list)
    rewritten_query: str | None = None
    trace_id: str | None = None
    variant_id: str | None = None
```

### 4.2 Standard State Schema (`StandardWorkflowState`)
```python
class StandardWorkflowState(TypedDict, total=False):
    question: str
    messages: Sequence[BaseMessage]
    attachments: list[dict[str, Any]]
    session_id: str | None
    trace_id: str | None
    answer: str
    retrieved_contexts: list[str]
    source_used: str
    citations: list[dict[str, Any]]
    trace: list[str]
    rewritten_query: str | None
```

### 4.3 Pluggable Workflow
`PluggableWorkflow` wraps a compiled graph or a factory callable `() -> CompiledStateGraph`, normalizing `WorkflowInput` $\to$ `StandardWorkflowState` and `StandardWorkflowState` $\to$ `WorkflowOutput`.

### 4.4 Execution Profiles & 3-Tier Precedence
```python
class ExecutionProfile(BaseModel):
    variant_id: str
    model_name: str | None = None
    prompt_version: str | None = None
    temperature: float | None = None
    parameters: dict[str, Any] = Field(default_factory=dict)
    tags: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
```
Resolution: `Explicit Request Overrides` > `Registered Profile` > `Workflow Baseline`.

### 4.5 Evaluator Seam Enhancement
```python
def evaluate(
    self,
    question: str,
    answer: str,
    source_used: str,
    retrieved_contexts: list[str],
    trace_id: str | None = None,
    *,
    expected_output: str | None = None,
    expected_source: str | None = None,
) -> EvaluationResult: ...
```
When `expected_source` is supplied, `source_accuracy = 1.0 if source_used == expected_source else 0.0`.
When `expected_output` is supplied, reference similarity/correctness is computed.

---

## 5. Verification & Testing Strategy
- **Unit Testing**:
  - `tests/rag/test_workflow_contracts.py`: Update `BaseWorkflowContractTest` to use `WorkflowInput` and test `PluggableWorkflow`.
  - `tests/rag/test_workflow_runner.py`: Verify translation between `ChatRequest` and `WorkflowInput`, 3-tier profile precedence, and Langfuse tagging.
  - `tests/rag/test_evaluation_engine.py`: Test `FakeEvaluator` and `EvaluationEngine` with `expected_output` and `expected_source`.
- **Integration Testing**:
  - `tests/test_experiment_runner.py`: Test matrix benchmarking execution and Langfuse dataset linking.
  - `tests/rag/test_registry_loader.py`: Test dynamic discovery of exported workflow packages from a temporary directory.
- **Regression Gates**:
  - Full test suite execution: `uv run pytest`.
