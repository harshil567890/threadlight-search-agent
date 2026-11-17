# Threadlight — design

## Brief and acceptance criteria

Build a fast, accurate chat agent with one tool, web search; remember what the user asked and when; deliver an accessible GitHub repository with this document.

| Requirement | Implementation | Verification |
| --- | --- | --- |
| One tool | Request contains only `tools: [{google_search: {}}]` | Provider payload assertion |
| Fast answers | Single model request; streamed output; no planning or memory-extraction model calls | Stream event tests; live latency measurement proposed below |
| Correctness | Freshness-aware search instructions, source annotations, explicit uncertainty | Citation contract test; human evaluation required |
| What and when | Persistent SQLite messages with exact UTC timestamps; recent context and cross-chat retrieval | Timestamp, restart-cookie, recall and deletion tests |
| Reviewable delivery | Source, setup guide, tests, design document | Public repository check |

## Product design

Visual thesis: a quiet, warm paper workspace with olive accents and editorial typography.
Content plan: conversation navigation, an uncluttered starter workspace, readable messages, and a persistent composer. No separate marketing page.
Interaction thesis: a brief entrance, subtle suggestion and send-button hover feedback, and progressive streamed text. Reduced-motion users get no animation. Enter sends; Shift+Enter inserts a newline.

## Architecture

```mermaid
sequenceDiagram
    participant B as Browser
    participant A as Flask application
    participant D as Local SQLite
    participant O as Gemini GenerateContent
    B->>A: Question + chat ID + browser timezone
    A->>D: Verify ownership; save question with UTC time
    A->>D: Recent chat + relevant past questions
    A->>O: Instructions + timestamped context + google_search only
    O-->>A: Text deltas / grounding metadata / finish reason
    A-->>B: Incremental NDJSON events
    A->>D: Persist completed answer and sources
    A-->>B: Completed reply + timestamp + duration
```

Flask serves the static UI and JSON/NDJSON API. SQLite uses WAL, foreign keys, parameterized statements, and owner/chat indexes. Requests streams the provider's SSE response; the server translates recognized events into small NDJSON messages. There are no frameworks for agent orchestration, vector services, background workers, or frontend build tools.

## Exactly one tool

The model can call only `google_search`. Gemini decides whether and how often to search; the app does not claim a per-request search-call limit. It cannot run code, read arbitrary files, write memory through a tool, or invoke other APIs. The backend automatically reads/writes conversational state. Google Search grounding handles search and supporting web content within that single tool type.

## Speed and accuracy

`gemini-4-hydrogen` is a configurable latency/cost-oriented default. Tool choice is automatic: memory questions and writing tasks avoid unnecessary search, while current or uncertain facts should trigger it. This routing is prompted, not a formal guarantee. No redundant model call classifies or summarizes the question. The generated output budget is 4,096 tokens, including any provider thinking budget, and network connect/read timeouts are 10/90 seconds. A read timeout is not an absolute wall-clock deadline.

Use primary sources, cite claims, distinguish inference, and acknowledge conflicting evidence. Citation links come from structured Gemini grounding chunks and supports and permit only HTTP(S) URLs. The answer is rendered using safe text nodes, with clickable inline citation spans and source links beneath it, preventing executable model HTML. Gemini's UTF-8 byte offsets are validated and converted to Unicode code-point offsets for the UI. The required provider-generated Search Suggestions HTML is persisted and displayed in a sandboxed iframe with scripts disabled and popups enabled for its links. Inline styles are allowed for this widget; the app itself still disallows inline scripts.

Streaming improves perceived latency, not model inference time. Sources and inline citation links appear after the completed response; streamed prose can precede final verification. Answers may still be wrong. Neither factual correctness nor search use is guaranteed by the prompt.

## Temporal memory

Every question is persisted before the provider call, even if that call fails. Answers are committed only after a clean stream end, `finishReason: STOP`, and nonempty public text. Partial output is not saved as a successful answer. Completion times are separate from question times. The model receives authoritative current server UTC time and a user-provided browser timezone; all stored times remain UTC, and the UI formats timestamps locally.

Current-chat context includes the latest 40 messages within 60,000 characters. Cross-chat recall considers the latest 2,000 user questions for this browser owner, ranks by lexical overlap and recency, combines 12 relevant with 8 recent questions, and bounds the injected memory text. These are exact historical questions, not model-generated facts or summaries. Current corrections should override earlier statements.

This deliberately favors simple, inspectable retrieval over embeddings. It can miss synonyms, older questions, and facts mentioned only by the assistant. It does not promise exhaustive recall. The full history stays available through the UI/export until deletion; only a bounded portion goes into each model request. A future improvement is FTS5/BM25 plus a rolling provenance-backed summary, evaluated before adding complexity.

## Isolation and privacy

A random 128-bit browser cookie identifies the local owner. Chats are owner-scoped on every read, delete, export, and inference. The cookie is HttpOnly, SameSite=Strict, and Secure under HTTPS. Clearing it loses access to prior history; this is not account authentication or cross-device sync. No user identity or owner ID is accepted from JSON bodies. A per-owner lock prevents simultaneous inference and deletion during a reply in this single-process deployment.

The app rejects cross-origin mutations, applies a restrictive CSP, validates message/body size, keeps keys server-side, and excludes `.env` and databases from Git. It does not log raw API errors or keys. Website text is instructed to be treated as untrusted; prompt injection resistance is probabilistic. Memory includes quoted historical user text, which can also contain adversarial instructions. Never use this design to expose privileged tools or secrets to the model.

Data is plaintext on the local server; selected history is sent to Google under the Gemini API data-use terms applicable to the user's service tier. Search may disclose search queries. Deletion cascades to message rows and removes them from future retrieval. SQLite pages and independent backups may retain recoverable data. There is no encryption, account recovery, public authentication, distributed locking, or internet-facing abuse protection. Localhost binding is intentional.

## Errors and limits

Missing keys yield a setup error without fake replies. Non-success provider HTTP responses, malformed streams, network errors, empty replies, blocked prompts, token-limited completions, and early EOF become explicit errors. Known Gemini denials are translated into specific remediation for unavailable free tiers, key restrictions, disabled APIs, model availability, and quota limits without echoing raw provider messages or keys. Failed questions remain timestamped in history; a retry is a new question with a new time. Browser disconnects release the owner lock as the stream closes; successful completion is not guaranteed after disconnect. After an interrupted answer, the UI removes the partial assistant text and restores the question for retry.

The Flask development server is for local use. A public service needs authentication, HTTPS, quotas, rate limits, a production WSGI server, shared locks, operational monitoring, retention controls, and backups. Multi-worker operation is not supported by the in-process lock.

## Evaluation

Automated tests use mocked streams to validate observable contracts without charges or network dependence: exactly one tool, citation extraction, complete-answer persistence, UTC timestamps, relevant cross-chat recall, cookie-based reconnection, owner isolation, deletion, validation, missing credentials, and incomplete-stream recovery, UTF-8 citation offsets, thought-part exclusion, and blocked/truncated Gemini responses. Existing SQLite databases gain a Search Suggestions column automatically without deleting messages. The key and model are read from the environment or `.env` on each request; no credential is sent to the browser or included in the request URL.

Before claiming production quality, run a versioned live evaluation set with at least 20 questions each for stable facts, current facts, multi-turn followups, cross-chat recall, temporal recall, and misleading/unanswerable prompts. Record model/version, date, question timestamp, expected answer/source, actual citations, search usage, time to first token, total duration, and token/search cost. Score factual support, citation validity, timestamp correctness, abstention, and recall precision/recall. Manually open sources; don't use model fluency as a correctness metric. Compare medians and p95 latencies for search and no-search separately. No live benchmark results are claimed in this repository.

## Provider references

[Gemini streaming REST API](https://ai.google.dev/api/generate-content#method:-models.streamgeneratecontent), [Google Search grounding](https://ai.google.dev/gemini-api/docs/google-search), and [default model](https://ai.google.dev/gemini-api/docs/models/gemini-4-hydrogen).
