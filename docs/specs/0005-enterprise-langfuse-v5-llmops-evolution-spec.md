# Technical Specification: Enterprise Langfuse v5 LLMOps Evolution & Infrastructure Consolidation

- **ADR Reference:** `docs/adr/0005-enterprise-langfuse-v5-llmops-evolution.md`
- **Specification Document:** `docs/specs/0005-enterprise-langfuse-v5-llmops-evolution-spec.md`
- **Target Version:** `0.5.0`
- **Status:** Approved / Ready for Implementation

---

## 1. System Overview

This specification operationalizes **ADR 0005** to evolve the RAG Engine into an enterprise-grade LLMOps architecture powered by **Langfuse v5**. The initiative consolidates infrastructure by decommissioning the redundant local SQLite audit database, instruments asynchronous live DeepEval evaluations without response latency penalties, introduces deterministic token and cost accounting, establishes dynamic prompt management via Langfuse Prompt CMS with fail-open local caching, enriches vector retrieval spans, and provides an automated dataset experimentation runner to benchmark `fix_single_llm` vs `fix_single_jev`.

```
┌────────────────────────────────────────────────────────────────────────────────────────────────────────┐
│                                            Client Layer                                                │
│                                                                                                        │
│   POST /api/chat         ─────────────┐                                                                │
│   POST /api/chat/stream  ─────────────┼──────────────────────────────┐                                 │
└───────────────────────────────────────┼──────────────────────────────┼─────────────────────────────────┘
                                        │                              │
                                        ▼                              ▼
┌────────────────────────────────────────────────────────────────────────────────────────────────────────┐
│                                        FastAPI Engine (Routes)                                         │
│                                                                                                        │
│   1. Contextvars & Telemetry Binding (extract_trace_id, LangfuseCallbackHandler)                       │
│   2. Dispatch Standalone Workflow (fix_single_llm.ask / fix_single_jev.ask)                           │
│   3. Response Returned to Client (Zero User Latency Impact)                                            │
│   4. Background Task Dispatch ───> run_online_evaluation_task()                                        │
│      - SSE Stream completion hook / BackgroundTasks                                                    │
│      - Sample Rate Gate (random.random() < settings.eval_sample_rate)                                  │
│      - DeepEval LLM Judge Evaluation (Faithfulness & Answer Relevancy)                                 │
│      - Langfuse Score Ingestion (client.create_score)                                                  │
└───────────────────────────────────────┬──────────────────────────────┬─────────────────────────────────┘
                                        │                              │
                        RunnableConfig  │                              │ Prompt Lookup
                        (Callbacks)     ▼                              ▼ (300s TTL Cache)
┌──────────────────────────────────────────────────┐      ┌──────────────────────────────────────────────┐
│       LangGraph Execution (Isolated Workflows)   │      │        Langfuse Prompt CMS                   │
│                                                  │      │        (app/services/prompts.py)             │
│   - fix_single_llm.py / fix_single_jev.py        │<────>│                                              │
│   - Retriever Spans: get_retriever(config)       │      │  FallbackPrompt (fail-open local dataclass)  │
│   - Web Search Spans: web_search_tool(config)    │      │  rag:router:* | rag:grade_* | rag:generate_*  │
│   - Custom JEV Spans: usage & cost details       │      │  Links PromptClient directly to observations │
└──────────────────────────────────────────────────┘      └──────────────────────────────────────────────┘
                                        │
                                        ▼
┌────────────────────────────────────────────────────────────────────────────────────────────────────────┐
│                              Offline & Benchmarking Infrastructure                                     │
│                                                                                                        │
│   - Decommissioned: app/services/audit.py & data/audit.db (rely on Langfuse + structlog)               │
│   - data/gold_benchmarks.json (10 curated canonical enterprise IT test cases)                          │
│   - evaluation/run_experiment.py (Langfuse Datasets & Experiments Runner: LLM vs JEV comparative run) │
└────────────────────────────────────────────────────────────────────────────────────────────────────────┘
```

---

## 2. File Change Manifest

| File | Action | Description / Purpose |
|---|---|---|
| `app/services/audit.py` | **Delete** | Decommissioned; SQLite audit trail replaced by Langfuse trace persistence and `structlog`. |
| `data/audit.db` | **Delete** | Local database removed to eliminate dual persistence and migration debt. |
| `tests/test_audit_migration.py` | **Delete** | Legacy SQLite schema migration test suite retired. |
| `app/core/config.py` | **Modify** | Remove `audit_db_path`. Add `eval_sample_rate`, `eval_metrics`, `eval_timeout_seconds`, `llm_cost_per_1k_input_tokens`, `llm_cost_per_1k_output_tokens`, `jev_cost_per_1k_input_tokens`, and `jev_cost_per_1k_output_tokens`. |
| `app/core/telemetry.py` | **Modify** | Enrich `get_langfuse_callback` with custom metadata/tagging support and expose helper utilities for prompt client access and explicit cost observation creation. |
| `app/services/prompts.py` | **Create** | Langfuse Prompt CMS client wrapper with 300s TTL cache, local mustache templating, fallback catalog (`LOCAL_PROMPTS`), and `FallbackPrompt` class. |
| `app/services/evaluation.py` | **Modify** | Implement `run_online_evaluation_task()` with sampling gate, direct answer context filtering, 30s timeout, and background score emission. |
| `app/rag/state.py` | **Modify** | Add `retrieved_contexts: list[str]` to `AgentState`. |
| `app/rag/fix_single_llm.py` | **Modify** | Thread `config: RunnableConfig` to retriever and web search nodes, integrate Prompt CMS for all prompts, populate `retrieved_contexts`, and link generation observations. |
| `app/rag/fix_single_jev.py` | **Modify** | Thread `config: RunnableConfig` to tools, integrate Prompt CMS for JEV/LLM prompts, compute deterministic `cost_details` in `_record_jev_generation`, and populate `retrieved_contexts`. |
| `app/main.py` | **Modify** | Remove `init_db()` from startup lifespan context manager. |
| `app/api/routes.py` | **Modify** | Remove `write_audit()` calls. Wire `run_online_evaluation_task` into `POST /api/chat` via `BackgroundTasks` and into `POST /api/chat/stream` post-completion. |
| `data/gold_benchmarks.json` | **Create** | 10 canonical golden benchmark cases covering all routing paths, KB retrieval, web fallback, query rewrite, and direct answers. |
| `evaluation/run_experiment.py` | **Create** | Automated benchmarking runner connecting to Langfuse Datasets (`it-support-benchmark`), executing both workflows side-by-side, running DeepEval, and outputting comparative scorecards. |
| `tests/test_chat_stream.py` | **Modify** | Remove legacy `write_audit` monkeypatches and verify streaming evaluation trigger. |
| `tests/test_feedback_api.py` | **Modify** | Remove legacy `write_audit` monkeypatches. |
| `tests/test_e2e_observability.py` | **Modify** | Clean up `init_db` assertions and verify Langfuse-only persistence path. |
| `tests/test_prompts_cms.py` | **Create** | Unit tests for `FallbackPrompt`, template compilation, cache TTL, and fail-open behaviors. |
| `tests/test_online_evaluation.py` | **Create** | Unit tests for evaluation worker, sampling rate checks, direct answer handling, and timeout safety. |
| `tests/test_experiment_runner.py` | **Create** | Tests verifying golden benchmark schema loading, dataset seeding logic, and experiment execution stubs. |

---

## 3. Configuration & Settings Contract (`app/core/config.py`)

Update `Settings` in `app/core/config.py` to remove deprecated SQLite fields and introduce LLMOps evaluation and cost controls:

```python
class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Core Application Settings
    app_name: str = "Enterprise RAG Engine"
    app_env: str = "development"
    debug: bool = False
    max_retries: int = 1

    # Provider & Model Settings
    llm_base_url: str = "https://api.gapgpt.com/v1"
    llm_api_key: SecretStr = SecretStr("mock-key")
    llm_model: str = "deepseek-v4-flash"

    jev_base_url: str = "https://api.typesafe.com/v1"
    jev_api_key: SecretStr = SecretStr("mock-key")
    jev_model: str = "typesafe/jev-1.13"

    embedding_base_url: str = "https://api.gapgpt.com/v1"
    embedding_api_key: SecretStr = SecretStr("mock-key")
    embedding_model: str = "text-embedding-3-small"

    tavily_api_key: SecretStr = SecretStr("mock-key")
    chroma_db_dir: str = "./data/chroma"

    # Langfuse Observability Settings
    langfuse_enabled: bool = False
    langfuse_public_key: Optional[str] = None
    langfuse_secret_key: Optional[SecretStr] = None
    langfuse_host: str = "https://cloud.langfuse.com"
    langfuse_debug: bool = False
    langfuse_prompt_cache_ttl_seconds: int = 300

    # Online Evaluation Settings
    eval_sample_rate: float = 1.0  # 0.0 to 1.0
    eval_metrics: list[str] = ["faithfulness", "answer_relevancy"]
    eval_timeout_seconds: float = 30.0

    # Unit Cost Settings (USD per 1,000 tokens)
    llm_cost_per_1k_input_tokens: float = 0.0015
    llm_cost_per_1k_output_tokens: float = 0.0020
    jev_cost_per_1k_input_tokens: float = 0.0008
    jev_cost_per_1k_output_tokens: float = 0.0008
```

---

## 4. Pillar 1: Decommissioning SQLite Audit Store

### 4.1 Removal Plan
1. Delete `app/services/audit.py` entirely.
2. Remove `data/audit.db` from disk and git tracking (ensure `.gitignore` ignores `data/*.db`).
3. Delete `tests/test_audit_migration.py`.
4. In `app/main.py`:
   - Remove `from app.services.audit import init_db`.
   - Remove `init_db()` from the `@asynccontextmanager async def lifespan(app: FastAPI)` function.
5. In `app/api/routes.py`:
   - Remove `from app.services.audit import write_audit`.
   - Remove all three calls to `write_audit(...)` in `/api/chat` and `chat_event_generator()`.

### 4.2 Logging Replacement
In `app/api/routes.py`, request lifecycle completion is logged via `structlog` without database writes:
```python
logger.info(
    "chat_query_completed",
    workflow=payload.workflow,
    question=payload.question,
    source_used=result["source_used"],
    trace_id=trace_id,
    trace_steps=result.get("trace", []),
    citations_count=len(result.get("citations", [])),
)
```

---

## 5. Pillars 2 & 3: Online DeepEval Scoring & Context Exposure

### 5.1 AgentState Schema Extension (`app/rag/state.py`)
```python
class AgentState(TypedDict):
    question: str
    current_query: str
    kb_docs: list[Document]
    web_results: str
    kb_grade: str
    web_grade: str
    answer: str
    source_used: str
    retry_count: int
    trace: list[str]
    citations: list[dict[str, str]]
    trace_id: NotRequired[str]
    retrieved_contexts: NotRequired[list[str]]
```

### 5.2 Context Population in Graph Nodes
- In `retrieve_kb(state: AgentState, config: RunnableConfig)`:
  ```python
  docs = get_retriever().invoke(state["current_query"], config=config)
  raw_contexts = [doc.page_content for doc in docs]
  return {
      "kb_docs": docs,
      "retrieved_contexts": raw_contexts,
      "trace": [f"Private KB retrieval → {len(docs)} chunks"],
  }
  ```
- In `search_web(state: AgentState, config: RunnableConfig)`:
  ```python
  # When extracting items from Tavily results:
  web_contexts = [f"Title: {item.get('title')}\nURL: {item.get('url')}\nContent: {item.get('content')}" for item in result.get("results", [])]
  return {
      "web_results": "\n\n".join(lines),
      "citations": citations,
      "source_used": "web",
      "retrieved_contexts": web_contexts,
      "trace": ["Web fallback → Tavily search"],
  }
  ```
- Workflow Return Contract: `fix_single_llm.ask()` and `fix_single_jev.ask()` must include `retrieved_contexts` in their returned dictionary:
  ```python
  return {
      "workflow": payload.workflow,
      "answer": result["answer"],
      "source_used": result["source_used"],
      "trace": result.get("trace", []),
      "citations": result.get("citations", []),
      "rewritten_query": result.get("current_query", payload.question),
      "trace_id": trace_id,
      "retrieved_contexts": result.get("retrieved_contexts", []),
  }
  ```

### 5.3 Asynchronous Evaluation Worker (`app/services/evaluation.py`)
Add `run_online_evaluation_task` with sampling, timeout, and metric branching:

```python
import asyncio
import logging
import random
from typing import Optional
from starlette.concurrency import run_in_threadpool
from app.core.config import get_settings
from app.services.evaluation import score_trace_faithfulness, score_trace_answer_relevancy

logger = logging.getLogger(__name__)

async def run_online_evaluation_task(
    trace_id: Optional[str],
    question: str,
    answer: str,
    source_used: str,
    retrieved_contexts: list[str],
) -> None:
    """Asynchronously evaluate live chat completions and push scores to Langfuse."""
    if not trace_id:
        return

    settings = get_settings()
    if not settings.langfuse_enabled:
        return

    # 1. Deterministic Sampling Gate
    if settings.eval_sample_rate <= 0.0:
        return
    if settings.eval_sample_rate < 1.0 and random.random() > settings.eval_sample_rate:
        return

    logger.info("Triggering online DeepEval evaluation for trace %s", trace_id)

    async def _evaluate() -> None:
        # Evaluate Faithfulness only when retrieval took place and context is present
        if "faithfulness" in settings.eval_metrics:
            if source_used != "direct" and retrieved_contexts:
                try:
                    await run_in_threadpool(
                        score_trace_faithfulness,
                        trace_id=trace_id,
                        question=question,
                        answer=answer,
                        retrieved_contexts=retrieved_contexts,
                    )
                except Exception as exc:
                    logger.warning("Faithfulness evaluation failed for trace %s: %s", trace_id, exc)
            else:
                logger.debug("Skipping faithfulness for direct or empty context trace %s", trace_id)

        # Evaluate Answer Relevancy
        if "answer_relevancy" in settings.eval_metrics:
            try:
                await run_in_threadpool(
                    score_trace_answer_relevancy,
                    trace_id=trace_id,
                    question=question,
                    answer=answer,
                )
            except Exception as exc:
                logger.warning("Answer relevancy evaluation failed for trace %s: %s", trace_id, exc)

    try:
        await asyncio.wait_for(_evaluate(), timeout=settings.eval_timeout_seconds)
    except asyncio.TimeoutError:
        logger.warning("Online evaluation timed out after %.1fs for trace %s", settings.eval_timeout_seconds, trace_id)
    except Exception as exc:
        logger.warning("Unexpected error during online evaluation for trace %s: %s", trace_id, exc)
```

### 5.4 Integration in API Endpoints (`app/api/routes.py`)
- **Synchronous Endpoint (`POST /api/chat`)**:
  ```python
  @router.post("/chat")
  async def chat(payload: ChatRequest, background_tasks: BackgroundTasks):
      # ... graph execution ...
      if trace_id:
          background_tasks.add_task(
              run_online_evaluation_task,
              trace_id=trace_id,
              question=payload.question,
              answer=result["answer"],
              source_used=result["source_used"],
              retrieved_contexts=result.get("retrieved_contexts", []),
          )
      return { ... }
  ```
- **Streaming Endpoint (`POST /api/chat/stream`)**:
  In `chat_event_generator()`, capture accumulated answer and contexts:
  ```python
  # When yielding StreamMetadataEvent at stream end:
  if trace_id:
      asyncio.create_task(
          run_online_evaluation_task(
              trace_id=trace_id,
              question=question,
              answer=accumulated_answer,
              source_used=final_source,
              retrieved_contexts=accumulated_contexts,
          )
      )
  ```

---

## 6. Pillar 4: Deterministic Token & Cost Accounting

### 6.1 Cost Calculation Formulas
Given token counts $T_{\text{in}}$ and $T_{\text{out}}$, and configured settings $C_{\text{in\_1k}}$ and $C_{\text{out\_1k}}$:

$$\text{Cost}_{\text{input}} = \frac{T_{\text{in}} \times C_{\text{in\_1k}}}{1000.0}$$
$$\text{Cost}_{\text{output}} = \frac{T_{\text{out}} \times C_{\text{out\_1k}}}{1000.0}$$
$$\text{Cost}_{\text{total}} = \text{Cost}_{\text{input}} + \text{Cost}_{\text{output}}$$

### 6.2 Injection in JEV Classifier Spans (`app/rag/fix_single_jev.py`)
Update `_record_jev_generation` to construct both `usage_details` and `cost_details`:

```python
def _record_jev_generation(
    name: str,
    input_text: str,
    output_text: str,
    trace_id: str | None = None,
    prompt_client: Any | None = None,
) -> None:
    try:
        client = get_langfuse_client()
        if not client:
            return

        resolved_trace_id = trace_id or structlog.contextvars.get_contextvars().get("trace_id")
        trace_context = {"trace_id": resolved_trace_id} if resolved_trace_id else None

        usage = _estimate_jev_tokens(input_text, output_text)
        settings = get_settings()
        cost_in = (usage["input"] * settings.jev_cost_per_1k_input_tokens) / 1000.0
        cost_out = (usage["output"] * settings.jev_cost_per_1k_output_tokens) / 1000.0
        cost_details = {
            "input": round(cost_in, 6),
            "output": round(cost_out, 6),
            "total": round(cost_in + cost_out, 6),
        }

        obs_kwargs: dict[str, Any] = {
            "name": name,
            "as_type": "generation",
            "model": settings.jev_model,
            "input": input_text,
            "output": output_text,
            "usage_details": usage,
            "cost_details": cost_details,
            "metadata": {
                "classifier": "typesafe_jev",
                "cost_unit": "USD",
            },
        }
        if prompt_client and hasattr(prompt_client, "version"):
            obs_kwargs["prompt"] = prompt_client
        if trace_context:
            obs_kwargs["trace_context"] = trace_context

        client.start_observation(**obs_kwargs).end()
    except Exception as exc:
        logger.debug("Failed to record JEV generation span: %s", exc)
```

---

## 7. Pillar 5: Langfuse Prompt CMS Integration (`app/services/prompts.py`)

### 7.1 FallbackPrompt Dataclass & Service Implementation
Create `app/services/prompts.py` implementing client-side caching with 300s TTL and fail-open local string fallbacks.

```python
import logging
import re
from dataclasses import dataclass
from typing import Any, Optional
from app.core.config import get_settings
from app.core.telemetry import get_langfuse_client

logger = logging.getLogger(__name__)

@dataclass
class FallbackPrompt:
    """Lightweight prompt interface compatible with Langfuse TextPromptClient."""
    name: str
    template: str
    version: int = 0

    def compile(self, **kwargs: Any) -> str:
        """Replace mustache {{variable}} placeholders with kwargs."""
        text = self.template
        for key, value in kwargs.items():
            pattern = re.compile(rf"\{\{\s*{re.escape(key)}\s*\}\}")
            text = pattern.sub(str(value), text)
        return text

    @property
    def prompt(self) -> str:
        return self.template


LOCAL_PROMPTS: dict[str, str] = {
    "rag:router:llm": """You route messages for an enterprise IT support assistant.
Use kb for questions about company IT policies, VPN, password reset, MFA, laptop setup,
software access, security, email, devices, troubleshooting, or technology support.
Use direct only for greetings, thanks, or casual chat that needs no company knowledge.
Question: {{question}}
Return valid JSON like {"route":"kb"}.""",

    "rag:router:jev": """Which path should this enterprise IT support question take?
Question: {{question}}
Options:
- kb: Questions about company IT policies, VPN, password reset, MFA, laptop setup, software access, security, email, devices, troubleshooting, or technology support.
- direct: Only greetings, thanks, or casual chat that needs no company knowledge.""",

    "rag:grade_kb:llm": """You grade evidence for an enterprise IT support assistant.

Original user question:
{{question}}

Query used for retrieval:
{{current_query}}

Private company KB evidence:
{{context}}

Evaluate whether the evidence is sufficient to answer the ORIGINAL user question confidently and specifically.
The retrieval query is only a search aid. Do not grade the evidence as good merely because it is relevant to the retrieval query.
Return good only if the evidence supports a sufficiently specific answer to the original question. Otherwise return weak.

Return valid JSON:
{"grade": "good"} or {"grade": "weak"}.""",

    "rag:grade_kb:jev": """Question: {{question}}

Private company KB evidence:
{{context}}

Is the provided private company KB evidence sufficient to answer the question confidently and specifically?""",

    "rag:grade_web:llm": """You grade web evidence for an enterprise IT support assistant.

Original user question:
{{question}}

Query used for web search:
{{current_query}}

Web evidence:
{{web_results}}

Evaluate whether the web evidence is sufficient, relevant, and specific enough to answer the ORIGINAL user question confidently.
The search query is only a retrieval aid. Do not grade the evidence as good merely because it matches the search query.
Use good only when the evidence directly supports a sufficiently specific answer to the original question. Otherwise, return weak.

Return valid JSON:
{"grade": "good"} or {"grade": "weak"}.""",

    "rag:grade_web:jev": """Question: {{question}}

Web evidence:
{{web_results}}

Is the external web documentation sufficient to answer the user question confidently?""",

    "rag:rewrite_query": """You improve search queries for an enterprise IT assistant.

Original user question:
{{question}}

Previous query:
{{current_query}}

Create a meaningfully improved query that preserves the original intent and addresses the missing information.
Do not answer the question. Return only the new query.""",

    "rag:generate_kb": """You are an enterprise IT support copilot. Answer ONLY from the private company KB below.
Be concise, actionable, and safe. If steps are present, present them clearly.
Do not invent policy details. Mention that the answer is based on the company's private knowledge base.
Question: {{question}}

Private KB:
{{context}}""",

    "rag:generate_web": """You are an enterprise IT support copilot. The private company KB was insufficient.
Answer ONLY from the web evidence below. Clearly say this is external web information and may need IT validation before changing company-managed systems.
Question: {{question}}

Web evidence:
{{web_results}}""",

    "rag:direct_answer": """Respond briefly, warmly, and naturally as an enterprise IT support copilot to: {{question}}""",
}


def get_workflow_prompt(name: str) -> Any:
    """Retrieve prompt from Langfuse Prompt CMS with local caching and fail-open fallback."""
    settings = get_settings()
    fallback_text = LOCAL_PROMPTS.get(name, "")

    client = get_langfuse_client()
    if client and settings.langfuse_enabled:
        try:
            return client.get_prompt(
                name=name,
                type="text",
                cache_ttl_seconds=settings.langfuse_prompt_cache_ttl_seconds,
                fallback=fallback_text,
            )
        except Exception as exc:
            logger.warning("Failed to fetch prompt '%s' from Langfuse Prompt CMS: %s. Using local fallback.", name, exc)

    return FallbackPrompt(name=name, template=fallback_text, version=0)
```

---

## 8. Pillar 6: Rich Retriever & Tool Observations in LangGraph

### 8.1 Node Signatures & RunnableConfig Threading
In both `app/rag/fix_single_llm.py` and `app/rag/fix_single_jev.py`, update retrieval and search nodes to receive `config: RunnableConfig`:

```python
from langchain_core.runnables import RunnableConfig

def retrieve_kb(state: AgentState, config: RunnableConfig | None = None):
    structlog.contextvars.bind_contextvars(step="retrieve_kb")
    docs = get_retriever().invoke(state["current_query"], config=config)
    return {
        "kb_docs": docs,
        "retrieved_contexts": [d.page_content for d in docs],
        "trace": [f"Private KB retrieval → {len(docs)} chunks"],
    }

def search_web(state: AgentState, config: RunnableConfig | None = None):
    structlog.contextvars.bind_contextvars(step="search_web")
    result = web_search_tool().invoke({"query": state["current_query"]}, config=config)
    # ... parse citations and results ...
    return {
        "web_results": "\n\n".join(lines),
        "citations": citations,
        "source_used": "web",
        "retrieved_contexts": web_contexts,
        "trace": ["Web fallback → Tavily search"],
    }
```

This guarantees that LangChain's callback handler triggers `on_retriever_start` / `on_retriever_end` and `on_tool_start` / `on_tool_end`, registering vector chunks, similarity scores, query text, and search hits as nested child observations in Langfuse.

---

## 9. Pillar 7: Langfuse Dataset Experimentation Runner

### 9.1 Benchmark Schema & Content (`data/gold_benchmarks.json`)
Create `data/gold_benchmarks.json` containing 10 curated enterprise test cases:

```json
[
  {
    "id": "tc-01-greeting",
    "input": "Hello! Good morning, how can you help me today?",
    "expected_output": "Warm greeting offering enterprise IT support assistance.",
    "expected_route": "direct",
    "expected_source": "direct",
    "category": "direct_conversation"
  },
  {
    "id": "tc-02-kb-vpn-pulse",
    "input": "How do I configure Pulse Secure VPN on my corporate MacBook?",
    "expected_output": "Install Pulse Secure from Self Service portal, connect to vpn.corp.internal, authenticate with corporate SSO and MFA.",
    "expected_route": "kb",
    "expected_source": "vectorstore",
    "category": "private_kb_retrieval"
  },
  {
    "id": "tc-03-kb-password-reset",
    "input": "Where do I go to change my Active Directory domain password?",
    "expected_output": "Navigate to identity.corp.internal/self-service and follow password complexity requirements.",
    "expected_route": "kb",
    "expected_source": "vectorstore",
    "category": "private_kb_retrieval"
  },
  {
    "id": "tc-04-kb-mfa-token",
    "input": "I lost my phone with Okta Verify. How can I regain access to corporate apps?",
    "expected_output": "Contact the IT Service Desk directly or visit the in-person Tech Bar for identity verification and temporary bypass code.",
    "expected_route": "kb",
    "expected_source": "vectorstore",
    "category": "private_kb_retrieval"
  },
  {
    "id": "tc-05-kb-guest-wifi",
    "input": "What is the procedure for granting temporary Wi-Fi access to an external vendor visiting next Tuesday?",
    "expected_output": "Sponsor a guest account via guest-wifi.corp.internal with visitor email and department cost center.",
    "expected_route": "kb",
    "expected_source": "vectorstore",
    "category": "private_kb_retrieval"
  },
  {
    "id": "tc-06-web-docker-error",
    "input": "How do I fix Docker error 'failed to solve with frontend dockerfile.v0' on Ubuntu 24.04?",
    "expected_output": "Enable BuildKit with DOCKER_BUILDKIT=1 or update Docker daemon configuration.",
    "expected_route": "kb",
    "expected_source": "web_search",
    "category": "web_fallback"
  },
  {
    "id": "tc-07-web-python-uv",
    "input": "What is the command to create an isolated virtual environment and add dependencies using the astral uv CLI tool?",
    "expected_output": "Use 'uv venv' to create the virtual environment and 'uv add <package>' to install dependencies.",
    "expected_route": "kb",
    "expected_source": "web_search",
    "category": "web_fallback"
  },
  {
    "id": "tc-08-rewrite-printer-vague",
    "input": "Office printer is broken and won't print my PDF.",
    "expected_output": "Clear print spooler, check paper tray status, or verify connection to printserver.corp.internal.",
    "expected_route": "kb",
    "expected_source": "vectorstore",
    "category": "query_rewrite"
  },
  {
    "id": "tc-09-insufficient-alien",
    "input": "What is the corporate travel reimbursement policy for interplanetary flights to Alpha Centauri?",
    "expected_output": "Insufficient evidence found in company knowledge base or web search. Please contact IT/HR help desk.",
    "expected_route": "kb",
    "expected_source": "insufficient_evidence",
    "category": "insufficient_evidence"
  },
  {
    "id": "tc-10-direct-farewell",
    "input": "Thanks, that solved my issue. Have a great day!",
    "expected_output": "You're welcome! Feel free to reach out if you need further IT assistance.",
    "expected_route": "direct",
    "expected_source": "direct",
    "category": "direct_conversation"
  }
]
```

### 9.2 Experiment Runner CLI (`evaluation/run_experiment.py`)
```python
"""Langfuse Dataset & Experimentation Runner for RAG Workflows.

Usage:
    uv run python evaluation/run_experiment.py [--dataset-name it-support-benchmark] [--workflows fix_single_llm,fix_single_jev]
"""
import argparse
import json
import logging
import time
from pathlib import Path
from typing import Any
from deepeval.metrics import AnswerRelevancyMetric, FaithfulnessMetric
from deepeval.test_case import LLMTestCase
from app.core.config import get_settings
from app.core.telemetry import get_langfuse_client
from app.rag import fix_single_llm, fix_single_jev
from evaluation.judge import judge

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("experiment_runner")
settings = get_settings()

WORKFLOWS = {
    "fix_single_llm": fix_single_llm.ask,
    "fix_single_jev": fix_single_jev.ask,
}

def load_gold_benchmarks() -> list[dict[str, Any]]:
    benchmark_file = Path("data/gold_benchmarks.json")
    if not benchmark_file.exists():
        raise FileNotFoundError(f"Missing benchmark file: {benchmark_file}")
    with open(benchmark_file, "r", encoding="utf-8") as f:
        return json.load(f)

def ensure_dataset(client: Any, dataset_name: str) -> Any:
    try:
        return client.get_dataset(dataset_name)
    except Exception:
        logger.info("Dataset '%s' not found in Langfuse. Seeding from data/gold_benchmarks.json...", dataset_name)
        dataset = client.create_dataset(name=dataset_name, description="Enterprise IT Support Golden Benchmark")
        benchmarks = load_gold_benchmarks()
        for item in benchmarks:
            client.create_dataset_item(
                dataset_name=dataset_name,
                input={"question": item["input"]},
                expected_output=item["expected_output"],
                metadata={
                    "id": item["id"],
                    "expected_route": item["expected_route"],
                    "expected_source": item["expected_source"],
                    "category": item["category"],
                },
            )
        logger.info("Successfully seeded %d items into Langfuse dataset '%s'", len(benchmarks), dataset_name)
        return client.get_dataset(dataset_name)

def main():
    parser = argparse.ArgumentParser(description="Run Langfuse Experimentation Benchmarks")
    parser.add_argument("--dataset-name", default="it-support-benchmark")
    parser.add_argument("--workflows", default="fix_single_llm,fix_single_jev")
    args = parser.parse_args()

    client = get_langfuse_client()
    if not client:
        logger.error("Langfuse client is not available. Please verify credentials in environment.")
        return

    dataset = ensure_dataset(client, args.dataset_name)
    target_workflows = [w.strip() for w in args.workflows.split(",") if w.strip() in WORKFLOWS]

    results_table = []
    run_timestamp = int(time.time())

    for wf_name in target_workflows:
        wf_ask = WORKFLOWS[wf_name]
        logger.info("=== Running Experiment for Workflow: %s ===", wf_name)
        run_name = f"{wf_name}-run-{run_timestamp}"

        total_latencies = []
        faithfulness_scores = []
        relevancy_scores = []

        for item in dataset.items:
            question = item.input.get("question", "") if isinstance(item.input, dict) else str(item.input)
            start_t = time.perf_counter()
            result = wf_ask(question)
            latency = time.perf_counter() - start_t
            total_latencies.append(latency)

            answer = result.get("answer", "")
            source_used = result.get("source_used", "")
            contexts = result.get("retrieved_contexts", [])

            # Run Faithfulness if contexts present
            faith_val = None
            if source_used != "direct" and contexts:
                try:
                    tc = LLMTestCase(input=question, actual_output=answer, retrieval_context=contexts)
                    fm = FaithfulnessMetric(threshold=0.5, model=judge.llm, async_mode=False)
                    fm.measure(tc)
                    faith_val = float(fm.score)
                    faithfulness_scores.append(faith_val)
                except Exception as exc:
                    logger.warning("Faithfulness calculation error: %s", exc)

            # Run Answer Relevancy
            rel_val = None
            try:
                tc = LLMTestCase(input=question, actual_output=answer)
                arm = AnswerRelevancyMetric(threshold=0.5, model=judge.llm, async_mode=False)
                arm.measure(tc)
                rel_val = float(arm.score)
                relevancy_scores.append(rel_val)
            except Exception as exc:
                logger.warning("Answer relevancy calculation error: %s", exc)

            # Link to Langfuse Experiment Run if available
            if hasattr(client, "create_score") and result.get("trace_id"):
                if faith_val is not None:
                    client.create_score(trace_id=result["trace_id"], name="faithfulness", value=faith_val)
                if rel_val is not None:
                    client.create_score(trace_id=result["trace_id"], name="answer_relevancy", value=rel_val)

        avg_lat = sum(total_latencies) / len(total_latencies) if total_latencies else 0.0
        avg_faith = sum(faithfulness_scores) / len(faithfulness_scores) if faithfulness_scores else 0.0
        avg_rel = sum(relevancy_scores) / len(relevancy_scores) if relevancy_scores else 0.0

        results_table.append({
            "Workflow": wf_name,
            "Items Evaluated": len(dataset.items),
            "Avg Latency (s)": round(avg_lat, 3),
            "Avg Faithfulness": round(avg_faith, 3),
            "Avg Relevancy": round(avg_rel, 3),
        })

    print("\n" + "=" * 65)
    print("           LANGFUSE BENCHMARK EXPERIMENT RESULTS")
    print("=" * 65)
    for r in results_table:
        print(f"Workflow: {r['Workflow']}")
        print(f"  - Avg Latency:       {r['Avg Latency (s)']}s")
        print(f"  - Avg Faithfulness:  {r['Avg Faithfulness']}")
        print(f"  - Avg Relevancy:     {r['Avg Relevancy']}")
        print("-" * 65)

if __name__ == "__main__":
    main()
```

---

## 10. TDD Verification Plan & Test Cleanup Checklist

### 10.1 Legacy Test Cleanup
1. **Delete `tests/test_audit_migration.py`**:
   Verify removal with `git rm tests/test_audit_migration.py`.
2. **Update `tests/test_chat_stream.py`**:
   Remove all `monkeypatch.setattr("app.api.routes.write_audit", ...)` lines and verify streaming events yield cleanly.
3. **Update `tests/test_feedback_api.py`**:
   Remove `mock_write_audit` fixtures and assertions.
4. **Update `tests/test_e2e_observability.py`**:
   Remove `init_db()` startup tests, replacing them with lifespan telemetry shutdown verification.

### 10.2 New Unit & Integration Tests

#### 1. `tests/test_prompts_cms.py`
- Test `FallbackPrompt.compile()` correctly substitutes multiple variables: `{{question}}`, `{{context}}`.
- Test `get_workflow_prompt()` returns `FallbackPrompt` when Langfuse is disabled or errors.
- Test `get_workflow_prompt()` returns `TextPromptClient` with `cache_ttl_seconds=300` when Langfuse client is active.
- Verify every key in `LOCAL_PROMPTS` compiles without syntax error against sample inputs.

#### 2. `tests/test_online_evaluation.py`
- Test `run_online_evaluation_task` does nothing when `trace_id` is None or `LANGFUSE_ENABLED=false`.
- Test sampling rate: with `eval_sample_rate = 0.0`, zero metrics are invoked.
- Test `source_used == "direct"`: assert `score_trace_faithfulness` is never invoked, but `score_trace_answer_relevancy` runs.
- Test judge failure / timeout resilience: mock a 35s sleep or Exception in `score_trace_faithfulness` and assert `run_online_evaluation_task` logs a warning and exits cleanly without raising.

#### 3. `tests/test_token_cost_accounting.py`
- Test JEV token estimation formula: verify 1 token per ~4 chars.
- Test JEV cost calculation with configured settings: input and output costs multiplied by `$0.0008 / 1000`.
- Verify `client.start_observation` receives dictionary `cost_details` matching `{"input": ..., "output": ..., "total": ...}`.

#### 4. `tests/test_experiment_runner.py`
- Verify `data/gold_benchmarks.json` contains exactly 10 valid JSON items with all mandatory fields (`id`, `input`, `expected_output`, `expected_route`, `expected_source`, `category`).
- Test dataset loading and mock client seeding logic.

---

## 11. Implementation Ticket Breakdown

- **Ticket 01**: Decommission SQLite Audit Store (`audit.py`, `data/audit.db`, `main.py` lifespan, and test clean-up).
- **Ticket 02**: Update Configuration Settings & Unit Cost Accounting in `app/core/config.py`.
- **Ticket 03**: Implement Langfuse Prompt CMS Service (`app/services/prompts.py`) & Fallback Catalog.
- **Ticket 04**: Instrument Vector Retriever & Tavily Search Spans in `fix_single_llm.py` and `fix_single_jev.py`.
- **Ticket 05**: Implement JEV Cost Accounting & Observation Linking in `fix_single_jev.py`.
- **Ticket 06**: Formalize `retrieved_contexts` in `AgentState` and Workflow Return State.
- **Ticket 07**: Build Asynchronous Online DeepEval Scoring Worker (`app/services/evaluation.py`) and Endpoint Wiring.
- **Ticket 08**: Create Benchmark Golden Dataset (`data/gold_benchmarks.json`) and Experiment Runner (`evaluation/run_experiment.py`).
- **Ticket 09**: Complete TDD Test Suite & E2E Validation.
