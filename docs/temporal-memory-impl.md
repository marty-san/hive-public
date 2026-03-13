# Temporal Memory — Implementation Spec

**Companion to**: `docs/temporal-memory-spec.md`
**Do not code from this document in isolation** — the design spec is the source of truth for decisions. This document translates those decisions into specific, ordered code changes.

---

## Existing Code Facts (Read Before Coding)

Relevant observations from the current codebase that affect implementation decisions:

- `AgentEpisodicMemory` already has `access_count` (Integer) and `last_accessed` (DateTime) columns. Do not re-add them. Do rename `last_accessed` → `last_accessed_at` in the model for consistency — but this requires a migration backfill (`UPDATE ... SET last_accessed_at = last_accessed`).
- Archiving is currently done by setting `confidence = 0.0`. This conflicts with the new validity model. After this implementation, archiving = `valid_until IS NOT NULL`. The `confidence > 0` filter in the dedup query must be migrated to `valid_until IS NULL`.
- `extra_data` JSON column already exists on `AgentEpisodicMemory`. Structured attributes (`keywords`, `tags`, `attribute_key`) can be stored here initially rather than adding separate columns, reducing migration complexity. The schema spec calls for proper columns eventually — this is a pragmatic shortcut for Stage 1.
- `_build_system_prompt` in `agent_service.py` assembles the memory context at lines 727–733. This is the exact location for the prompt formatting change.
- Memory extraction is called at line 554 in `generate_agent_response`, triggered only when `last_message.sender_type == "human"`. The prospective reflection hook goes in the same conditional block, after extraction.
- No conversation-complete hook exists in `chat.py` yet. The discussion loop in `run_discussion_loop` (somewhere in `chat.py`) is where the reflection trigger should live — find the point where `discussion_complete` is set to True.
- The consolidation trigger fires every 10 memories (line 405 in `memory_service.py`). That trigger condition should also gate on memory type — only count mutable types.

---

## Files Changed

| File | Change type |
|------|-------------|
| `backend/alembic/versions/006_add_temporal_memory.py` | New migration |
| `backend/app/models/memory.py` | Add columns to `AgentEpisodicMemory` |
| `backend/app/config.py` | Add tuning constants |
| `backend/app/services/memory_service.py` | Core logic changes (multiple functions) |
| `backend/app/services/agent_service.py` | Prompt formatting + reflection trigger |
| `backend/app/api/routes/chat.py` | Prospective reflection hook on discussion end |

---

## Task 1: Alembic Migration

**File**: `backend/alembic/versions/006_add_temporal_memory.py`
**Revision chain**: `revision = '006'`, `down_revision = '005'`

### `upgrade()` must add these columns to `agent_episodic_memories`:

| Column | SQLAlchemy type | Nullable | Server default |
|--------|----------------|----------|---------------|
| `valid_from` | DateTime | True | None |
| `valid_until` | DateTime | True | None |
| `asserted_at` | DateTime | True | None |
| `asserted_until` | DateTime | True | None |
| `superseded_by` | String | True | None |
| `occurred_at` | DateTime | True | None |
| `importance` | SmallInteger | True | None |

### Backfill SQL (run after `add_column` calls, still inside `upgrade()`):
```
UPDATE agent_episodic_memories
SET valid_from = created_at,
    asserted_at = created_at
WHERE valid_from IS NULL
```

### Column rename:
```
ALTER TABLE agent_episodic_memories
RENAME COLUMN last_accessed TO last_accessed_at
```

Note: SQLite does not support `RENAME COLUMN` in older versions. Use `op.alter_column` with SQLAlchemy — or if SQLite version is < 3.25, use the copy-table approach. Check SQLite version on target environment first.

### `downgrade()` must drop all seven new columns and rename `last_accessed_at` back to `last_accessed`.

---

## Task 2: Model Update

**File**: `backend/app/models/memory.py`
**Class**: `AgentEpisodicMemory`

### New imports needed:
- `SmallInteger` from `sqlalchemy`

### Columns to add (in order, after existing `embedding` column):

```
valid_from       DateTime, nullable
valid_until      DateTime, nullable
asserted_at      DateTime, nullable
asserted_until   DateTime, nullable
superseded_by    String, nullable
occurred_at      DateTime, nullable
importance       SmallInteger, nullable
```

### Column rename:
Change `last_accessed = Column(DateTime, nullable=True)` to `last_accessed_at = Column(DateTime, nullable=True)`.

### Comment update on `memory_type`:
Change the inline comment from `# 'fact', 'event', 'observation'` to `# 'fact_state', 'observation_preference', 'event', 'decision', 'rejection', 'reflection_summary'` — the new taxonomy. Old values (`'fact'`, `'observation'`) remain valid in the DB; they just won't be written by new code.

### No changes to `AgentSemanticMemory` or `AgentWorkingMemory` in this task.

---

## Task 3: Config Constants

**File**: `backend/app/config.py`

Add the following constants to the `Settings` class (or as module-level constants if the pattern used elsewhere is module-level). These must be defined here, never inline in service code:

```
# Temporal memory scoring weights
MEMORY_WEIGHT_SIMILARITY: float = 0.65       # Semantic similarity weight (all types)
MEMORY_WEIGHT_IMPORTANCE: float = 0.20       # Importance score weight (mutable types)
MEMORY_WEIGHT_FRESHNESS: float = 0.15        # Freshness decay weight (fact_state only)
MEMORY_WEIGHT_ACTIVATION: float = 0.20       # Activation weight (observation_preference only)

# Decay lambdas (half-life = ln(2) / lambda)
MEMORY_LAMBDA_STATE: float = 0.02            # ~35 day half-life for fact_state
MEMORY_LAMBDA_PREFERENCE: float = 0.005     # ~140 day half-life for observation_preference

# Importance normalization denominator
MEMORY_IMPORTANCE_MAX: int = 10

# Supersession
MEMORY_SUPERSESSION_SIMILARITY_THRESHOLD: float = 0.75

# Mutable memory types (subject to decay and supersession)
MEMORY_MUTABLE_TYPES: list = ["fact_state", "observation_preference", "fact", "observation"]

# Immutable memory types (full weight, never superseded)
MEMORY_IMMUTABLE_TYPES: list = ["event", "decision", "rejection", "reflection_summary"]
```

Note: `"fact"` and `"observation"` are included in `MEMORY_MUTABLE_TYPES` for backwards compatibility with existing rows. All new writes use `"fact_state"` and `"observation_preference"`.

---

## Task 4: `memory_service.py` — Core Changes

This is the largest task. Break it into the following sub-tasks, in order.

---

### 4a. Add `_compute_memory_score()` helper (new private method)

**Where**: New private method on `MemoryService`, before `retrieve_relevant_memories`.

**Signature**: `_compute_memory_score(self, memory, similarity: float, now: datetime) -> float`

**Logic**:
1. Determine the memory's decay class from `memory.memory_type` using `settings.MEMORY_MUTABLE_TYPES` and `settings.MEMORY_IMMUTABLE_TYPES`.
2. Normalize importance: `norm_importance = (memory.importance or 5) / settings.MEMORY_IMPORTANCE_MAX`
3. **If immutable type**:
   ```
   score = (settings.MEMORY_WEIGHT_SIMILARITY * similarity) + (settings.MEMORY_WEIGHT_IMPORTANCE * norm_importance)
   ```
   Return score.
4. **If `fact_state` or `fact`**:
   - Compute age in days: `age = (now - (memory.valid_from or memory.created_at)).days`
   - `freshness = exp(-settings.MEMORY_LAMBDA_STATE * age)`
   - `score = (settings.MEMORY_WEIGHT_SIMILARITY * similarity) + (settings.MEMORY_WEIGHT_IMPORTANCE * norm_importance) + (settings.MEMORY_WEIGHT_FRESHNESS * freshness)`
   - Return score.
5. **If `observation_preference` or `observation`**:
   - Compute days since last access: use `memory.last_accessed_at` if not null, else use `memory.created_at`
   - `recency_boost = exp(-settings.MEMORY_LAMBDA_PREFERENCE * days_since_access)`
   - `activation = math.log(1 + memory.access_count) * recency_boost`
   - Normalize activation to 0–1: cap `activation` at 5.0 before normalizing (empirical ceiling)
   - `norm_activation = min(activation, 5.0) / 5.0`
   - `score = (settings.MEMORY_WEIGHT_SIMILARITY * similarity) + (settings.MEMORY_WEIGHT_IMPORTANCE * norm_importance) + (settings.MEMORY_WEIGHT_ACTIVATION * norm_activation)`
   - Return score.

**Import needed**: `import math` at top of file (or use `from math import exp, log`).

---

### 4b. Add `_detect_temporal_query()` and `_extract_time_range()` helpers (new private methods)

**`_detect_temporal_query(self, query: str) -> bool`**

Returns True if the query contains temporal reference language. Check for the presence of any of these indicator patterns (case-insensitive):
- "last week", "last month", "last year", "last quarter"
- "in january", "in february", ... (any month name)
- "in 2024", "in 2025", "in 2026"
- "before launch", "after launch", "before v2", "after v2"
- "when we", "at the time", "as of", "back when"
- "yesterday", "two weeks ago", "a month ago", "six months ago"

Simple string matching is sufficient — no regex required. Return True on any match.

**`_extract_time_range(self, query: str, db: AsyncSession) -> dict`**

Async method. Makes a Haiku call to extract a structured time range from the query.

Prompt to Haiku:
> "Extract the time range implied by this query. Return a JSON object with optional 'after' and 'before' keys, each an ISO 8601 date string. If no time range is present, return {}.\n\nQuery: {query}\n\nJSON:"

Parse the response. Return a dict like `{"after": "2025-01-01", "before": "2025-04-01"}` or `{}` if parsing fails.

On any exception, log a warning and return `{}` — this is a non-critical enhancement.

---

### 4c. Update `retrieve_relevant_memories()`

**Current signature**:
```python
async def retrieve_relevant_memories(
    self, agent_id, query, db, conversation_id=None, limit=5, time_window_days=None
)
```

**New signature** (add `mode` parameter):
```python
async def retrieve_relevant_memories(
    self, agent_id, query, db, conversation_id=None, limit=5, time_window_days=None,
    mode="current"
)
```

**Changes to DB query conditions**:

Replace the current `conditions` list with mode-aware filtering:

- `mode="current"` (default):
  - Filter: `agent_id == agent_id AND (valid_until IS NULL OR valid_until is not set yet — i.e., confidence > 0 for old rows)`
  - The filter must handle both old rows (no `valid_until` column value, but `confidence > 0`) and new rows (`valid_until IS NULL`).
  - Use: `AND (valid_until IS NULL)`
  - This works for both: old rows have `valid_until = NULL` after the migration backfill; new superseded rows have `valid_until` set.

- `mode="as_of"`:
  - Requires a `as_of_dt: datetime` parameter (add to signature when implementing this mode).
  - Filter: `valid_from <= as_of_dt AND (valid_until IS NULL OR valid_until >= as_of_dt)`

- `mode="range"`:
  - Requires `range_start: datetime`, `range_end: datetime` parameters.
  - Filter: `valid_from <= range_end AND (valid_until IS NULL OR valid_until >= range_start)`

- `mode="timeline"`:
  - Requires `attribute_key: str` parameter.
  - Filter: `attribute_key == attribute_key`, no `valid_until` filter
  - Order: `valid_from ASC` instead of `created_at DESC`
  - Skip semantic scoring — return all versions in order

For the initial implementation, only `mode="current"` is required. The other modes can be stubbed with a `NotImplementedError` and implemented as a follow-on.

**Changes to scoring**: Replace the current `scored_memories.sort(key=lambda x: x[1], reverse=True)` block with:

```python
now = datetime.utcnow()
scored_memories = []
for memory in all_memories:
    if memory.embedding:
        similarity = embedding_service.cosine_similarity(query_embedding, memory.embedding)
        if similarity >= min_similarity:
            score = self._compute_memory_score(memory, similarity, now)
            scored_memories.append((memory, score))

scored_memories.sort(key=lambda x: x[1], reverse=True)
```

**Update access metadata**: After returning results, fire a background update to increment `access_count` and set `last_accessed_at = now()` on retrieved memories. This must NOT block the return — use `asyncio.create_task()` or a fire-and-forget pattern. The update query targets the IDs of the returned memories only.

**Time-aware query expansion**: Before building the DB query, add:
```python
if self._detect_temporal_query(query):
    time_range = await self._extract_time_range(query, db)
    if time_range:
        # Switch to as_of or range mode based on what was extracted
        # Log the detected time range for debugging
```
This is additive — if time range extraction returns `{}`, proceed with the normal `current` mode query.

---

### 4d. Update `store_episodic_memory()`

**New parameters to add**:
```python
attribute_key: Optional[str] = None,
importance: Optional[int] = None,
keywords: Optional[List[str]] = None,
tags: Optional[List[str]] = None,
valid_from: Optional[datetime] = None,
occurred_at: Optional[datetime] = None,
```

**Before the existing dedup check**, add the type-gate:

```
If memory_type is in settings.MEMORY_IMMUTABLE_TYPES:
    Skip dedup entirely.
    Set asserted_at = datetime.utcnow()
    Set valid_from = valid_from or datetime.utcnow()
    Store and return immediately.
```

**Replace the existing dedup block** for mutable types:

The current logic:
```python
if similarity > 0.92:
    return existing  # drop the new memory silently
```

New logic:
1. If `attribute_key` is provided:
   - Query for active memories (`valid_until IS NULL`) with the same `agent_id` AND `attribute_key` (stored in `extra_data["attribute_key"]` — see note below).
   - If a match is found: this is a state update.
     - Set `existing.valid_until = valid_from or datetime.utcnow()`
     - Set `existing.asserted_until = datetime.utcnow()`
     - Set `existing.superseded_by = new_memory.id` (set after creating new memory)
     - Store the new memory first, then update the old one.
   - If no match: proceed to create normally.

2. If `attribute_key` is not provided: fall back to the existing similarity-based dedup.
   - Keep the 0.92 threshold for exact duplicates on untyped memories.
   - For similarities between 0.75 and 0.92 on mutable types: this is the Haiku fallback zone.
     - Make the Haiku call: "Memory A: [existing]. Memory B: [new]. Does B update or contradict A, or say the same thing? Answer UPDATE or SAME."
     - If UPDATE: supersede (set `valid_until`, `asserted_until`, `superseded_by`).
     - If SAME: discard as before.

**Storing structured attributes**: Store `attribute_key`, `keywords`, `tags`, and `importance` into the `extra_data` JSON field (since this is a Stage 1 implementation that avoids adding more columns):
```python
memory.extra_data = {
    "attribute_key": attribute_key,
    "importance": importance,
    "keywords": keywords or [],
    "tags": tags or [],
}
```
Additionally, set the new `importance` column directly: `memory.importance = importance`.

**Set temporal fields on the new memory object**:
```python
memory.valid_from = valid_from or datetime.utcnow()
memory.asserted_at = datetime.utcnow()
memory.occurred_at = occurred_at  # will be None for most non-event types
```

**Add to the logger.info call**: include `attribute_key`, `importance`, `memory_type` in the log fields.

---

### 4e. Update `extract_memories_from_conversation()`

This is the highest-leverage change. The extraction prompt must be rewritten to produce typed, attributed output.

**New extraction prompt** (replace the existing `extraction_prompt` variable):

```
You are extracting memorable facts from a conversation to store in an agent's long-term memory.

MEMORY TYPES:
- fact_state: A current-state claim about the project or world that may change over time.
  Example: "The backend uses FastAPI." / "The project is in beta with 12 customers."
- observation_preference: A pattern or preference about how the user works.
  Example: "The user prefers written specs before implementation begins."
- event: Something that happened at a specific point in time (immutable historical fact).
  Example: "Version 2 launched on March 15." / "The team moved to remote-first in Q1."
- decision: A deliberate choice made, with reasoning. Store the reasoning too.
  Example: "Decided to use SQLite over Postgres — simplicity is the priority at this scale."
- rejection: An idea that was explicitly ruled out, with reasoning.
  Example: "Ruled out microservices. Team too small to maintain separate services."

RULES:
- Only extract actual facts or decisions the user PROVIDED — not questions or requests.
- Decisions and rejections are the most valuable type. Capture the reasoning, not just the conclusion.
- For fact_state, assign an attribute_key: a 2-4 word snake_case identifier for what the fact is about.
  Examples: "backend_framework", "project_phase", "team_size", "primary_language"
- Rate importance 1-10 (10 = critical architectural decision; 1 = minor detail).
- If you cannot find 2-3 distinct memorable facts, return an empty array.

Return ONLY a JSON array. Each item:
{
  "type": "<memory_type>",
  "content": "<clear, standalone statement>",
  "attribute_key": "<snake_case_key or null for non-fact_state>",
  "importance": <1-10>,
  "keywords": ["<keyword1>", "<keyword2>"],
  "tags": ["<tag1>"],
  "occurred_at": "<ISO date if event, else null>"
}

Conversation:
{conversation}

JSON array:
```

**Parse the response**: Replace the current line-by-line parsing with JSON parsing:
```python
import json
memories_data = json.loads(response["content"].strip())
# Handle markdown code fences if present
```

**Call `store_episodic_memory` with the new parameters**:
```python
await self.store_episodic_memory(
    agent_id=agent_id,
    content=item["content"],
    memory_type=item["type"],
    source_message_id=...,
    source_conversation_id=conversation_id,
    db=db,
    confidence=1.0,
    attribute_key=item.get("attribute_key"),
    importance=item.get("importance"),
    keywords=item.get("keywords"),
    tags=item.get("tags"),
    occurred_at=parse_iso(item.get("occurred_at")),  # helper to safely parse or return None
)
```

**Update the consolidation trigger** at line ~405: change the count query from `confidence > 0` to `valid_until IS NULL`. Also add a type filter so only mutable memories count toward the threshold:
```python
AND memory_type IN ('fact_state', 'observation_preference', 'fact', 'observation')
```

---

### 4f. Update `consolidate_episodic_memories()`

**Type filter on initial fetch**: Add `AND memory_type IN (mutable types)` to the query. Immutable types never enter the cluster pool.

**Superseded memory filter**: Change `confidence > 0` to `valid_until IS NULL` in the query conditions.

**Sort cluster by temporal order**: After building each cluster, sort by `memory.valid_from or memory.created_at` ascending before passing to the LLM.

**Updated consolidation prompt**: Replace the existing `consolidation_prompt` string. Add:
- Include timestamps in the cluster text: prefix each memory with `[{relative_time_ago}]`
- Add directive: "Memories are listed oldest to newest. Synthesize toward the most recent state. If entries represent a state change, describe the current state and note the transition."

**Pass forward attribute key**: When creating the `AgentSemanticMemory` from consolidation, also store the source `attribute_key` values in its `extra_data` field so the consolidated memory retains traceability.

---

### 4g. Add `trigger_prospective_reflection()` (new method)

**When it runs**: Called as a background task after a discussion session ends. Not on the hot path.

**Signature**: `async def trigger_prospective_reflection(self, agent_id: str, conversation_id: str, db: AsyncSession) -> None`

**Logic**:
1. Fetch all episodic memories with `source_conversation_id == conversation_id` created in the last session (use the conversation's last message timestamp as the boundary).
2. If fewer than 3 memories from this session, skip.
3. Group memories by their `tags` (from `extra_data`). Each tag group is a topic.
4. For each topic group with at least 2 memories:
   a. Build a summary text listing all the memories in the group.
   b. Call Haiku with: "Summarize the following related facts from a project discussion into 1-2 sentences of context. Focus on what was decided or established.\n\n{memory_texts}\n\nSummary:"
   c. Create a new `AgentEpisodicMemory` with:
      - `memory_type = "reflection_summary"`
      - `content = <haiku summary>`
      - `valid_from = datetime.utcnow()`
      - `importance = max(importance values in the group)`
      - `extra_data = {"tags": [topic], "source_memory_ids": [list of source IDs], "time_span": <days covered>}`
      - `source_conversation_id = conversation_id`
5. Log how many reflections were created.

Note: this function must be safe to call multiple times for the same conversation (idempotent). Add a check: skip if a `reflection_summary` with `source_conversation_id == conversation_id` already exists.

---

## Task 5: `agent_service.py` — Prompt Formatting

**File**: `backend/app/services/agent_service.py`

### 5a. Update `_build_system_prompt()` — episodic memory section

**Location**: Lines 728–733 (the `episodic_context` block).

**Current code** (flat list):
```python
episodic_context = "\n\n## Recent Relevant Information\n"
for i, memory in enumerate(memories, 1):
    episodic_context += f"{i}. {memory.content}\n"
```

**New code**: Split memories into two lists based on type class, then format as structured JSON blocks with relative time labels.

Helper needed: `_format_relative_time(dt: datetime) -> str`
- < 1 day: "today"
- 1–6 days: "X days ago"
- 7–29 days: "X weeks ago"
- 30–364 days: "X months ago"
- >= 365 days: "over X years ago"

Build two lists:
- `history_memories`: type in `settings.MEMORY_IMMUTABLE_TYPES` (sorted by `valid_from` or `created_at`, oldest first)
- `current_memories`: type in `settings.MEMORY_MUTABLE_TYPES` (sorted by `valid_from` or `created_at`, newest first)

Format each list as a compact JSON-like block (not full JSON — this is injected into a system prompt, not parsed). Use the format:

```
## Memory Context

**Project history** (decisions, rejections, events — these are permanent records):
- [14 months ago | decision | importance:9] Decided to use SQLite over Postgres — simplicity is priority at this scale.
- [8 months ago | rejection | importance:8] Ruled out microservices. Team too small to maintain separate services.

**Current state** (recent facts and preferences):
- [3 weeks ago | fact_state] The project is in beta with 12 active customers.
- [1 week ago | observation_preference] The user prefers written specs before implementation begins.
```

If `history_memories` is empty, omit that section. If `current_memories` is empty, omit that section. If both are empty, omit the entire `episodic_context`.

**Add reading instruction** after the memory block:
```
When drawing on memory: note the time qualifier of each item. History items are permanent records — never assume they are outdated. Current state items reflect conditions as of the stated time — newer information in this conversation takes precedence.
```

### 5b. Update the debug event in `generate_agent_response()`

**Location**: The `memory_retrieval` debug event at line ~378.

Add `memory_type` and `valid_from` to the `memories` array in the debug payload so the frontend can eventually show temporal metadata in the debug panel.

### 5c. Add prospective reflection trigger

**Location**: Inside the `if last_message and last_message.sender_type == "human":` block at line ~547, after the `extract_memories_from_conversation` call.

The reflection runs as a fire-and-forget background task using `asyncio.create_task()`. It should NOT block the response return or be awaited inline.

However: the reflection needs a DB session. `asyncio.create_task` with an async DB session is tricky — the session may close before the task runs. Pattern to use:
- Accept that reflection runs within the same request's DB session scope for now.
- Alternatively, move the trigger to the discussion-complete hook in `chat.py` where a fresh session can be opened.

The recommended approach: **move the trigger to `chat.py`**, not `agent_service.py`. See Task 6.

---

## Task 6: `chat.py` — Discussion-Complete Hook

**File**: `backend/app/api/routes/chat.py`

### 6a. Find the discussion-complete point

Locate where `discussion_complete = True` causes the discussion loop to exit. This is where the prospective reflection trigger should live.

### 6b. Add reflection trigger

After the loop exits (successfully, not via interrupt), and before the final WebSocket broadcast:

1. Collect all `agent_ids` that participated in the discussion.
2. For each agent, fire `asyncio.create_task(reflection_task(agent_id, conversation_id))` where `reflection_task` opens its own DB session and calls `memory_service.trigger_prospective_reflection(...)`.

Opening a fresh DB session inside a background task requires using `async_session_factory()` directly (not `get_db()` which is request-scoped). The pattern exists in the codebase if background tasks are used elsewhere — check; if not, document this as a DB session pattern decision that needs to be made.

3. Do NOT await these tasks. They run after the response is sent.

4. Log: `"prospective_reflection_tasks_queued"` with `agent_count` and `conversation_id`.

---

## Task 7: Update Existing Dedup Filter References

**File**: `backend/app/services/memory_service.py`

Global find-and-replace within this file: anywhere that filters `AgentEpisodicMemory.confidence > 0` as a proxy for "not archived", add `AgentEpisodicMemory.valid_until.is_(None)` as an additional condition. Both conditions should be present during the transition period (some old rows may have `confidence = 0` from old archiving; some new rows use `valid_until` instead).

Lines to update:
- Line 63: dedup check query (`confidence > 0`)
- Line ~400: consolidation count query (`confidence > 0`)
- Consolidation cluster query at line ~130: add `valid_until IS NULL`

---

## Implementation Order

Do these tasks in sequence. Each task is independently testable.

1. **Task 1** (migration) — schema change only, no behavior change. Run and verify all existing rows have `valid_from` populated.
2. **Task 2** (model) — update Python model to match migration. No behavior change.
3. **Task 3** (config constants) — define all tuning values in one place.
4. **Task 4a** (scoring helper) — new function, not yet called. Unit-testable in isolation.
5. **Task 7** (dedup filter) — low-risk update to existing filters. Run existing tests.
6. **Task 4c** (retrieval update) — replaces scoring, adds `valid_until` filter. This is the first user-visible change: stale superseded memories stop appearing (even though no supersession has happened yet, the filter is correct and future-safe).
7. **Task 4e** (extraction prompt) — new memories start arriving with typed, attributed data. Most important behavioral change.
8. **Task 4d** (store update) — attribute-key supersession becomes active. Requires 4e to have run first so memories have `attribute_key` values.
9. **Task 4f** (consolidation update) — type-aware consolidation.
10. **Task 5a** (prompt formatting) — agent prompt now shows history/current split.
11. **Task 5b** (debug event update) — minor.
12. **Task 4g** (prospective reflection method) — new method, not yet called.
13. **Task 5c / Task 6** (reflection trigger) — wire up the reflection trigger.
14. **Tasks 4b / time-aware query expansion** — the temporal query detection and Haiku expansion. Implement last since it adds latency and is an enhancement, not a correctness fix.

---

## Testing Notes

### After Task 6 (retrieval update):
- Create two `fact_state` memories for the same agent with different `valid_until` states (set one manually to verify filter).
- Confirm the superseded one (with `valid_until` set) does not appear in retrieve results.
- Confirm immutable types appear regardless of age.

### After Task 4e + 4d (extraction + supersession):
- Start a conversation. State a fact ("The project uses Flask"). In a later message, state an update ("We migrated the API to FastAPI").
- After both messages, query `agent_episodic_memories` directly.
- Expected: Flask memory has `valid_until` set; FastAPI memory has `valid_until = NULL`. Both rows exist.
- Retrieve memories for the agent: only FastAPI memory appears.

### After Task 5a (prompt formatting):
- Trigger an agent response where the agent has both decision/rejection memories and recent fact memories.
- Inspect the system prompt (via debug logs or a debug endpoint).
- Expected: two sections present, history items sorted oldest-first, current items sorted newest-first.

### Institutional memory persistence test:
- Create a `rejection` memory with `created_at` set to 12 months ago (insert directly or write a test fixture).
- Retrieve memories with a semantically related query.
- The rejection should appear at or near the top despite its age.

---

## Resolved Decisions

1. **SQLite RENAME COLUMN**: SQLite version is 3.50.4 — well above the 3.25 minimum. Simple `op.alter_column` rename works. No table-copy workaround needed.

2. **Background task DB session pattern**: No existing background tasks in the codebase currently open their own session. Pattern to use for the reflection task: import `AsyncSessionLocal` from `app.database` (already exported) and open a session inside the task with `async with AsyncSessionLocal() as session: ... await session.commit()`. The FastAPI server process runs continuously, so tasks can safely run after the request session closes. Each background task manages its own short-lived session for the duration of its work.

3. **Haiku supersession fallback latency**: Confirmed acceptable. `store_episodic_memory` is called from `extract_memories_from_conversation`, which is called *after* the agent response is returned to the user (line ~554 in `agent_service.py`, inside the post-response block). It is off the hot path.

4. **`importance` dual-write**: Store `importance` only in its dedicated column. `extra_data` holds `attribute_key`, `keywords`, and `tags` only — no redundant `importance` key.
