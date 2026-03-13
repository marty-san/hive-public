# Temporal Memory System — Design Spec

**Status**: Proposal
**Scope**: Upgrade to `memory_service.py`, DB schema, and agent prompt formatting
**Constraint**: Non-destructive. All existing memories remain valid.
**Research basis**: Generative Agents (Park et al.), LongMemEval, Graphiti/Zep, Mem0, RMM, A-Mem, MemInsight, bitemporal data modeling (Fowler, XTDB)

---

## Core Design Principle

Long-lived agents accumulate two fundamentally different kinds of knowledge, and the memory system must treat them differently:

**Mutable state** — what is currently true about a project:
> "The backend uses FastAPI." / "The project is in early beta with 12 customers."

These go stale. Newer state should outrank older state on the same topic.

**Immutable history** — what happened, what was decided, what was tried:
> "We evaluated microservices and rejected it because the team is too small to maintain it."
> "Subscription pricing was explicitly ruled out by the client."

These never expire. A rejection from two years ago is just as relevant today. Applying decay to these memories would be actively harmful — it is the main failure mode this spec is designed to avoid.

The goal is not selective forgetting. It is **contextually correct retrieval**: agents should know everything that ever happened, but with explicit temporal orientation so they can distinguish "what was once true" from "what is true now."

A secondary principle, adopted from the bitemporal database literature, is to track **two timelines**:
- **Effective time**: when the fact was true in the world or project
- **System time**: when the system learned about and believed the fact

These diverge in practice (an agent may record an event a week after it happened; a retroactive correction may update a fact's effective window). Keeping them separate enables auditing, "as of" queries, and retroactive corrections without touching the history.

---

## Problem Statement

The current system has four failure modes as projects evolve over time:

1. **Stale facts surface as relevant.** A memory stored last month saying "the homepage uses a carousel" outranks one from yesterday saying "the carousel was removed" if their semantic similarity to the query is similar. The agent confidently references something that no longer exists.

2. **State updates are silently rejected as duplicates.** The deduplication threshold (0.92 cosine similarity) was designed to prevent storing the same fact twice. But "the API is built with Flask" and "the API was migrated to FastAPI" are semantically close and may score above 0.92. The new, correct fact is thrown away and the old one remains active.

3. **Consolidation erases temporal ordering.** When episodic memories are consolidated, the LLM synthesizes them into a single statement without knowing which are old and which are new. A cluster containing both the old and new API framework gets averaged into something ambiguous.

4. **No distinction between "what is currently true" and "what was decided."** Both are stored as undifferentiated facts. Long-running projects lose the ability to say "we already tried that and here's why it didn't work."

---

## Goals

- Recent state facts outrank old ones on the same topic during retrieval.
- When a newer state fact contradicts an older one on the same attribute, the older one is superseded at the DB level and excluded from default queries.
- Decisions, rejections, and events are permanent records — they never decay, are never superseded, and are never consolidated away.
- Agents can answer "what did we believe in January?" or "what did we decide after we launched v2?" via explicit time-slice retrieval.
- The system retains full history for auditability — nothing is hard-deleted.
- All changes are additive. Existing data continues to work without migration except schema additions.

---

## Non-Goals

- Full temporal knowledge graph with entity linking. This spec is Stage 1 and Stage 2 of a three-stage roadmap (see end of document). Entity-level graph operations are Stage 3.
- Academic TKG embedding methods (TComplEx, TNTComplEx, ATiSE). Overkill at this scale.
- Forgetting in any form. The system re-ranks and filters, but never deletes.
- Synchronous write-path reflection. Prospective reflection (see Section 7) runs as an async background job after sessions, not during agent responses.

---

## 1. Memory Type Taxonomy

The `memory_type` field is extended from its current values (`fact`, `event`, `observation`) to a five-type taxonomy. Two decay classes govern retrieval behavior.

| Type | Description | Decay Class | Can Be Superseded? |
|------|-------------|-------------|-------------------|
| `fact_state` | A current-state claim about the project or world | Mutable | Yes |
| `observation_preference` | A pattern or preference about how the human works | Mutable (slow) | Yes |
| `event` | Something that happened at a specific point in time | Immutable | No |
| `decision` | A deliberate choice made, with reasoning | Immutable | No |
| `rejection` | An idea that was considered and explicitly ruled out | Immutable | No |
| `reflection_summary` | A synthesized topic summary derived from other memories | Derived | No |

**Mutable types** are subject to recency scoring and supersession.
**Immutable types** are retrieved at full weight indefinitely; they cannot be modified, superseded, or consolidated.
**Derived type** (`reflection_summary`) is an index — it surfaces relevant history but never replaces source memories.

### Migration note for existing data

Existing rows with `memory_type = 'fact'` map to `fact_state`. Existing `observation` rows map to `observation_preference`. Existing `event` rows remain as `event`. The spec recommends a one-time backfill rename when the extraction prompt is updated, rather than a hard schema constraint, so old data remains readable.

### Type classification examples

```
fact_state:           "The backend uses FastAPI."
fact_state:           "The project is in early beta with 12 active customers."
observation_preference: "The user prefers written specs before implementation begins."
observation_preference: "Responses without bullet points are preferred."
event:                "Version 2 launched on March 15."
event:                "The team moved to remote-first work in Q1 2025."
decision:             "Decided to use SQLite over Postgres — simplicity is the priority at this scale."
rejection:            "Ruled out subscription pricing. Client explicitly does not want recurring billing."
rejection:            "Rejected microservices approach. Team too small to maintain separate services."
reflection_summary:   "As of end of Q1: project pivoted from B2C to B2B; pricing model finalized."
```

---

## 2. Schema

### 2a. New columns on `AgentEpisodicMemory`

**Bitemporal columns** (effective time vs. system time):

| Column | Type | Default | Semantics |
|--------|------|---------|-----------|
| `valid_from` | DateTime, nullable | `created_at` (backfill) | When this fact became true in the world |
| `valid_until` | DateTime, nullable | NULL | When this fact stopped being true; NULL = currently valid |
| `asserted_at` | DateTime, nullable | `created_at` (backfill) | When the system began treating this as active knowledge |
| `asserted_until` | DateTime, nullable | NULL | When the system retired this from active knowledge |
| `superseded_by` | String, nullable | NULL | ID of the memory that replaced this one (mutable only) |

`valid_*` is effective time — when it was true in the world.
`asserted_*` is system time — when the system believed and used it.
Most rows will have matching values for both timelines, but they diverge when an event is recorded retroactively or when a supersession is applied with a backdated `valid_until`.

**Event-time columns** (for `event` type):

| Column | Type | Default | Semantics |
|--------|------|---------|-----------|
| `occurred_at` | DateTime, nullable | NULL | When the event actually happened (may differ from `created_at`) |
| `occurred_until` | DateTime, nullable | NULL | End of event window (null for point events) |

Agents frequently learn about events after they happen. `occurred_at` stores the real-world time; `created_at` stores when the agent recorded it.

**Access metadata** (for salience and retrieval tuning):

| Column | Type | Default | Semantics |
|--------|------|---------|-----------|
| `last_accessed_at` | DateTime, nullable | NULL | When this memory was last retrieved |
| `access_count` | Integer | 0 | How many times this memory has been retrieved |

Recency of retrieval is a better proxy for ongoing relevance than age at write time. A memory that keeps being retrieved is still useful; one that has never been accessed after storage may be less important. This also tracks what agents are actually using.

**Structured attributes** (for typed supersession and better retrieval):

| Column | Type | Default | Semantics |
|--------|------|---------|-----------|
| `importance` | SmallInteger, nullable | NULL | LLM-assigned 1–10 importance at write time |
| `keywords` | JSON (string array), nullable | NULL | Key terms extracted at write time |
| `tags` | JSON (string array), nullable | NULL | Categorical labels (e.g., ["tech-stack", "backend"]) |
| `attribute_key` | String, nullable | NULL | Normalized subject+attribute identifier for supersession |

`attribute_key` is the most important new field for supersession logic. It identifies what a mutable fact is *about* — for example, "backend_framework" or "project_phase" — so that two facts about the same attribute can be compared without relying on string heuristics. It is null for immutable types.

---

## 3. Write-Time Pipeline

### 3a. Updated extraction prompt

The extraction prompt in `extract_memories_from_conversation` must be updated to produce typed, attributed memories at write time. This is the highest-leverage change in the entire spec — correct classification at write time is what makes all downstream behavior correct.

Each extracted memory should return:

```json
{
  "type": "fact_state",
  "content": "The backend uses FastAPI.",
  "attribute_key": "backend_framework",
  "importance": 7,
  "keywords": ["FastAPI", "backend", "framework"],
  "tags": ["tech-stack"],
  "valid_from": null,
  "occurred_at": null
}
```

The `valid_from` and `occurred_at` fields are populated when the conversation contains explicit time references ("as of last week", "we launched on March 15"). When no time reference is present, `valid_from` defaults to `asserted_at` (the current time) and `occurred_at` stays null.

The `attribute_key` is a normalized 2–5 word identifier the LLM assigns to describe what a mutable fact is *about*, not what it says. Two facts with the same `attribute_key` are candidates for supersession. Examples: `"backend_framework"`, `"project_phase"`, `"team_size"`, `"primary_language"`. Immutable types leave this null.

`importance` (1–10) reflects how consequential the fact is to the project. A decision to rule out a major architectural approach is a 9. A detail about a single endpoint is a 3.

### 3b. Supersession via attribute key and temporal overlap

Supersession in the current spec relies on semantic similarity plus heuristic phrase matching ("was", "migrated", "no longer"). This is fragile for subtle contradictions. The updated approach follows Graphiti's model:

**When storing a new mutable memory:**

1. If `attribute_key` is null, proceed to deduplication as before (0.92 similarity threshold for exact duplicates).

2. If `attribute_key` is set, query for existing active memories with the same `attribute_key` and the same `agent_id` whose effective time overlaps with the new memory's `valid_from`.

3. If a match is found, it means the same attribute has a prior value that was valid at overlapping times. This is a state update:
   - Set `old.valid_until = new.valid_from` (the old value was true up until the new value takes over)
   - Set `old.asserted_until = now()` (the system is retiring it from the active view now)
   - Set `old.superseded_by = new.id`
   - Store the new memory normally

4. If no `attribute_key` match, fall back to semantic similarity deduplication for same-topic exact duplicates.

This approach is reliable and cheap: it's a DB lookup, not a string heuristic or LLM call. It handles the "Flask → FastAPI" case cleanly without needing to detect temporal language in the text. It also correctly handles retroactive updates: if a user says "oh, we actually switched frameworks two weeks ago," `valid_from` can be set to that past date and supersession will correctly backdate the transition.

For ambiguous cases where `attribute_key` is null and semantic similarity is between 0.75 and 0.92, a Haiku fallback call can still be used:
> "Memory A: [old]. Memory B: [new]. Does B update or contradict A, or say the same thing? Answer UPDATE or SAME."

But this should be the exception, not the primary path. Well-typed memories with `attribute_key` set will rarely need it.

**Immutable types are never superseded.** The code path that sets `valid_until` must gate on `memory_type not in ('event', 'decision', 'rejection', 'reflection_summary')`.

---

## 4. Retrieval Design

### 4a. Retrieval modes

The current `retrieve_relevant_memories` function has one mode: semantic similarity over all active memories. This is insufficient for time-referenced queries.

Add a `mode` parameter:

| Mode | Behavior |
|------|----------|
| `current` (default) | Active mutable state + all relevant immutable history |
| `as_of` | Memories whose effective time overlaps a given timestamp |
| `range` | Memories overlapping a given time interval |
| `timeline` | All versions of a given `attribute_key`, sorted by `valid_from` |

`as_of` and `range` queries enable questions like "what did we believe about the tech stack in January?" or "what happened between the v1 and v2 launches?" These are direct SQL-level filters on `valid_from` / `valid_until`.

`timeline` mode is for inspecting the full history of a single attribute — useful for debugging and for agent prompts that need to explain how something changed over time.

### 4b. Time-aware query expansion

Some queries have implicit temporal references that should trigger retrieval filtering rather than relying on semantic similarity alone. LongMemEval demonstrates that extracting a time range from the query and applying it as a filter improves recall on temporal reasoning questions.

Add a pre-retrieval step:

1. Detect whether the query contains temporal language ("last week", "before launch", "in Q1", "when we first started")
2. If detected, run a lightweight Haiku call to extract a structured time reference: `{"before": "2025-03-01"}` or `{"after": "2025-01-01", "before": "2025-04-01"}`
3. Rewrite the retrieval as an `as_of` or `range` query before running semantic search

This step is only triggered when temporal language is detected. It is not run on every query. The `current` mode default is still the most common case.

### 4c. Scoring formula

The scoring formula is per-type, not a single global decay:

**`fact_state`** (mutable truth claims):
```
score = w_sim * semantic_similarity
      + w_imp * normalized_importance
      + w_fresh * freshness_score

freshness_score = exp(-λ_state * age_days)
```
- `w_sim = 0.65`, `w_imp = 0.20`, `w_fresh = 0.15`
- `λ_state = 0.02` (half-life ~35 days)
- This is a **weak tie-breaker**, not a strong decay. Freshness provides only 15% of the score. Semantic relevance + importance dominate.

The default retrieval already filters out superseded facts via `valid_until IS NULL`. The freshness term handles the case where two unsuperseded facts about the same general domain exist (one recent, one old) and the agent should prefer the newer one.

**`observation_preference`** (user preferences, communication style):
```
score = w_sim * semantic_similarity
      + w_imp * normalized_importance
      + w_act * activation_score

activation_score = log(1 + access_count) * recency_boost(last_accessed_at)
recency_boost(t) = exp(-λ_pref * days_since_last_access)
```
- `w_sim = 0.65`, `w_imp = 0.15`, `w_act = 0.20`
- `λ_pref = 0.005` (half-life ~140 days — preferences change slowly)
- Preferences that are frequently retrieved score higher because they are demonstrably being used

**Immutable types** (`event`, `decision`, `rejection`, `reflection_summary`):
```
score = w_sim * semantic_similarity + w_imp * normalized_importance
```
- `w_sim = 0.75`, `w_imp = 0.25`
- No freshness or activation component. Age is irrelevant.

**Tuning:** All weights and `λ` values should be module-level constants. The recommended values here are starting points — they should be adjusted based on observed agent behavior. The weights intentionally keep freshness as a secondary signal so that highly relevant older facts are not crowded out.

### 4d. Active-view filtering

The DB query for `current` mode adds:
```sql
AND (valid_until IS NULL)
```

This is the most important single filter. A superseded fact never surfaces in normal retrieval, regardless of its semantic similarity or freshness score. Immutable types are never superseded, so they pass this filter unconditionally.

When `last_accessed_at` is updated on retrieval, use a background write (fire-and-forget) rather than blocking the retrieval response.

---

## 5. Consolidation: Type-Aware, Temporally-Ordered

Changes to `consolidate_episodic_memories`:

1. **Exclude immutable types from clustering.** `event`, `decision`, `rejection`, and `reflection_summary` are never consolidated. Only `fact_state` and `observation_preference` participate.

2. **Sort cluster by `valid_from` ascending** (falling back to `created_at`) before passing to the LLM. The LLM receives the cluster in chronological order with timestamps included.

3. **Update consolidation prompt** to include: "These memories are listed oldest to newest. Synthesize toward the most recent state. If the entries represent a state change, describe the current state and note what changed."

4. **Include attribute keys in consolidation output.** The consolidated semantic memory should carry forward the `attribute_key` from the cluster source so it remains supersession-aware.

5. **Before clustering:** check whether any memories in a candidate cluster have already been superseded. Exclude superseded memories (`valid_until IS NOT NULL`) from the input. The consolidation should reflect the current state, not a blend of current and retired states.

---

## 6. Agent Prompt Formatting

Retrieved memories are injected as structured JSON, split by class. This follows the LongMemEval finding that structured formats with explicit typing improve agent reasoning on temporal questions.

```json
{
  "project_history": [
    {
      "type": "rejection",
      "content": "Ruled out microservices architecture. Team too small to maintain separate services.",
      "recorded": "14 months ago",
      "importance": 9
    },
    {
      "type": "decision",
      "content": "Decided to use SQLite over Postgres — simplicity is the priority at this stage.",
      "recorded": "8 months ago",
      "importance": 7
    },
    {
      "type": "event",
      "content": "Version 2 launched. Backend migrated to FastAPI.",
      "occurred": "3 months ago",
      "importance": 8
    }
  ],
  "current_state": [
    {
      "type": "fact_state",
      "content": "The project is in beta with 12 active customers.",
      "valid_from": "3 weeks ago",
      "importance": 6
    },
    {
      "type": "observation_preference",
      "content": "The user prefers written specs before implementation begins.",
      "recorded": "2 weeks ago",
      "importance": 7
    }
  ]
}
```

The agent system prompt should include a two-step reading instruction:

> "Before responding, review the memory context. For each item in `project_history`, note whether it constrains or informs your response. For each item in `current_state`, note the time qualifier and treat it as the current ground truth unless you have more recent information in the conversation."

This is the Chain-of-Note pattern from LongMemEval — forcing the agent to extract relevant notes before composing its response improves accuracy when many memory items are present.

---

## 7. Prospective Reflection (Async Background Job)

Inspired by Reflective Memory Management (RMM), this is a periodic job that runs after a session ends, not during live agent responses.

**What it does:** After a conversation completes, a background job examines the episodic memories produced in that session, groups them by topic, and creates `reflection_summary` memories that serve as topic-level indexes.

**What it produces:** A `reflection_summary` memory for each topic discussed, with:
- `content`: A 1–3 sentence synthesis of what was covered or decided on this topic in this session
- `valid_from`: Set to the session end time
- `tags`: The topics covered
- No `attribute_key` (reflections are indexes, not facts)
- `importance`: Average of the source memories' importance

**What it does NOT do:**
- Replace or archive any source memories
- Supersede any existing memories
- Run synchronously during the discussion loop

**Why this helps:** Over many sessions, the raw episodic store grows large. Reflections provide high-level retrieval handles — "the Q1 planning session where we finalized pricing" — that help surface relevant history without scanning hundreds of raw memories. They are especially useful as a first-pass in the `current` retrieval mode before doing fine-grained semantic search.

---

## 8. Migration Plan

All changes are additive. Existing data remains valid throughout.

**Step 1 — Schema migration (Alembic):**
- Add all new columns with nullable defaults (no required backfill on existing rows)
- Backfill: `valid_from = created_at`, `asserted_at = created_at` for existing rows
- `access_count = 0`, all other new columns NULL for existing rows

**Step 2 — Code changes (in priority order):**

| Priority | Change | Why first? |
|----------|--------|------------|
| 1 | Update extraction prompt to output typed, attributed memories | All downstream logic depends on correct type and `attribute_key` at write time |
| 2 | Add `valid_until IS NULL` filter to `retrieve_relevant_memories` | Immediately fixes stale fact surfacing for any superseded memories |
| 3 | Implement attribute-key-based supersession in `store_episodic_memory` | Fixes the "state update rejected as duplicate" failure mode |
| 4 | Update scoring formula with importance + per-type decay | Improves retrieval quality; relies on importance being populated from Step 1 |
| 5 | Add retrieval modes (`as_of`, `range`, `timeline`) + time-aware query expansion | Unlocks temporal queries; lower urgency than correctness fixes |
| 6 | Update agent prompt formatting to JSON + two-section structure | Improves agent reasoning on temporal questions |
| 7 | Update consolidation to exclude immutable types and add temporal ordering | Prevents consolidation from averaging old and new state |
| 8 | Implement prospective reflection background job | Quality-of-life over time; not needed for initial correctness |

---

## 9. Evaluation

### What to test

Three categories map directly to the failure modes in the Problem Statement:

**State update acceptance:**
- Store `fact_state` A with `attribute_key = "backend_framework"` and value "Flask"
- Store `fact_state` B with the same `attribute_key` and value "FastAPI"
- Retrieve with `mode="current"`: B should appear, A should not
- Query A's row: `valid_until` should be set, `superseded_by` should point to B

**Time-slice correctness:**
- For the same `attribute_key`, query with `mode="as_of"` and a timestamp between A's `valid_from` and B's `valid_from`
- Should return A (it was valid at that time)
- Query with timestamp after B's `valid_from`
- Should return B

**Institutional memory persistence:**
- Store a `rejection` memory with any age
- Retrieve with `mode="current"` on a semantically related query
- The rejection should appear at full score regardless of how old it is
- Verify it is never returned from a supersession operation

### Tuning signals to watch

- `λ_state` too high: recent fact_state memories crowd out older, still-accurate ones on long-lived stable facts. Reduce λ.
- `λ_state` too low: outdated unsuperseded facts (those that slipped past supersession detection) linger. The primary fix is better `attribute_key` coverage, not raising λ.
- `w_fresh` too high: any scenario where a semantically highly relevant old decision is ranked below a weakly relevant new fact.
- Access count / activation: if agents are retrieving the same memories repeatedly, `activation_score` will amplify them strongly. This is usually correct (they're being used) but monitor for feedback loops.

---

## 10. Staged Roadmap

### Stage 1 — Current spec (this document)
Core temporal correctness. No structural changes beyond additive columns and logic changes.

- Bitemporal schema columns
- Memory type taxonomy + type-gated decay/supersession
- Attribute-key-based supersession
- Retrieval modes + time-aware query expansion
- Per-type scoring with importance
- JSON prompt formatting + Chain-of-Note reading
- Prospective reflection background job

### Stage 2 — Memory evolution (future)
Quality-durability as the memory store matures.

- A-Mem-style memory evolution: when a new memory arrives, update `keywords` and `tags` on related memories to reflect the enriched context
- MemInsight-style attribute mining: periodic jobs that extract and prioritize attributes from clusters, enabling better `attribute_key` coverage for memories stored before this system was implemented
- Cross-agent institutional memory: a shared project-level memory store that any agent can read and that carries decisions and rejections visible to all agents (currently each agent has a separate store)

### Stage 3 — Graph transition (optional future)
If Stage 1/2 attribute-key supersession still produces inconsistencies at scale.

- Introduce a minimal entity-and-attribute graph layer following Mem0's conflict invalidation model
- Full bitemporal temporal KG following Graphiti's architecture if enterprise-scale multi-entity relational memory is needed

The Stage 3 path is not a forgone conclusion. The attribute-key approach in Stage 1 handles the majority of state-update cases without graph infrastructure. Stage 3 becomes relevant if the agent needs to reason about relationships between entities (e.g., "what was the relationship between the team and the client at the time of the v2 launch?"), not just the current value of a named attribute.

---

## Open Questions

1. **Backfilling `attribute_key` for existing memories.** Current rows have no `attribute_key`. A one-time Haiku pass could extract attribute keys from existing `fact_state` memories, enabling attribute-key-based supersession retroactively. Risk: misclassification. The pragmatic choice is to leave existing rows without `attribute_key` and let new extractions populate it. The semantic similarity deduplication path handles old rows; attribute-key supersession handles new ones.

2. **`observation_preference` decay rate.** `λ_pref = 0.005` (~140 day half-life) is a guess. If the project is highly dynamic and user preferences change frequently, a shorter half-life is appropriate. If preferences are stable, the access-count activation signal may be enough and λ can approach zero.

3. **Cross-agent supersession.** If Agent A stores `backend_framework = "Flask"` and later Agent B stores `backend_framework = "FastAPI"`, neither will supersede the other because supersession is per-agent-id. Stage 2's shared project memory store resolves this. For now, agents may hold contradictory mutable state that only gets corrected if they individually receive the update.

4. **Time-aware query expansion model quality.** The LongMemEval paper notes that the quality of the extracted time range depends on the model doing the extraction. A Haiku call for time extraction may be unreliable for complex relative expressions ("two sprints after we moved to remote work"). The fallback is to use the full `current` mode, which still returns correct results — the time-aware expansion is an additive improvement, not a correctness requirement.
