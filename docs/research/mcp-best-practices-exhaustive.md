# MCP Best Practices (Comprehensive Research Summary)

> This document consolidates widely shared best practices for Model Context Protocol (MCP) servers from public specs, SDKs, community guides, and provider documentation. It also translates those practices into concrete, EMS-specific recommendations for this repository.

## Executive Summary

MCP servers are most effective when they:
- Expose action-oriented tools with clear, stable schemas and strong discovery workflows.
- Emphasize safety, least-privilege access, and robust error handling.
- Provide predictable performance through pagination, caching, and rate-limit resilience.
- Offer reliable observability (structured logs, request IDs, and metrics).
- Document user journeys with examples that mirror real user tasks.

The EMS MCP server already follows many of these principles (notably discovery-first workflows). The remaining recommendations focus on tightening security, resilience, and documentation consistency across tools.

## Protocol-Level Best Practices

### 1. Treat MCP as a Contract
- Use explicit, stable tool schemas (name, parameters, descriptions).
- Keep tool signatures backward compatible; add optional parameters instead of breaking ones.
- Prefer semantic versioning in your server, especially when tools change behavior.

### 2. Clear Separation of Responsibilities
- **Tools**: perform actions.
- **Resources**: offer read-only data blobs or documents.
- **Prompts**: encode curated guidance or reusable system prompts for common flows.

### 3. Capability Discovery
- Ensure clients can list tools/resources/prompts and receive concise descriptions.
- Include data models or short examples in tool descriptions to reduce ambiguity.

### 4. Minimal, Deterministic Side Effects
- Avoid tools that silently mutate state.
- Make effects explicit in tool descriptions and error messages.

### 5. Backward Compatibility Strategy
Common strategies:
- Deprecate tools but keep them functional for a time window.
- Introduce new tools rather than repurposing old names.
- Add new optional parameters with defaults that preserve behavior.

## Tool Design Best Practices

### 1. Action-Oriented Naming (RPC Style)
- Use verbs: `list_databases`, `search_fields`, `query_database`.
- Avoid REST-like naming or vague nouns that hide the action.

### 2. Strong Discovery Workflows
- Require ID discovery rather than guessing (e.g., list databases → list fields → query).
- Encourage structured sequences in examples and docstrings.

### 3. Parameter Clarity
Include:
- Allowed values for enums.
- Default values and when they apply.
- Validation rules and examples.

### 4. Safe Defaults
Defaults should:
- Limit result size (pagination or limit).
- Prefer safe display formats over raw/unbounded data.
- Avoid heavy operations when the user intent is unclear.

### 5. Error Messages as Guidance
Return errors that:
- Explain why the request failed.
- Provide a hint for remediation (e.g., “Use search_fields('altitude')”).

## Data Safety & Security

### 1. Least Privilege and Scope
- Limit tokens to only the necessary EMS capabilities.
- Enforce per-user or per-role access when possible.

### 2. Secrets Handling
- Read secrets from environment variables or managed secret stores.
- Never store credentials in config files, logs, or error payloads.

### 3. Prompt-Injection Mitigation
Defenses include:
- Treat all tool inputs as untrusted.
- Validate parameters (types, ranges, whitelist).
- Avoid executing user-controlled strings in privileged contexts.

### 4. Data Minimization
- Return only what the user needs.
- Redact sensitive fields by default; allow opt-in for privileged users.

## Reliability & Performance

### 1. Retry Strategy
Use bounded retries with exponential backoff:
- Retry on transient errors (429, 5xx).
- Respect `Retry-After` headers when present.
- Avoid retrying on 4xx errors other than 429.

### 2. Pagination
Large responses should be paginated:
- Accept `limit` and `offset` or cursor-based pagination.
- Provide total counts when feasible for UI planning.

### 3. Caching
Cache stable data like:
- EMS system lists
- database hierarchies
- field metadata
Use TTL-based caches to avoid stale data.

### 4. Concurrency & Rate Limiting
- Guard concurrent authentication calls with locks.
- Enforce server-side throttling where the upstream API is sensitive.

## Observability & Ops

### 1. Structured Logging
Include:
- request IDs
- tool name
- duration
- success/failure status

### 2. Metrics & Health
Expose:
- latency percentiles
- error rates per tool
- authentication failures

### 3. Traceability
Include correlation IDs between MCP requests and EMS API calls to simplify debugging.

## Documentation & UX

### 1. User-Centered Examples
Document flows like:
- “Find all flights with altitude above X”
- “Fetch analytics for a given tail number and flight phase”

### 2. Troubleshooting Guides
Include:
- common auth errors
- network connectivity
- invalid field IDs or query formats

### 3. Prompt Templates
Provide curated prompts for common workflows (search → query → analytics).

## Testing & Quality

### 1. Contract Tests
- Validate tool schemas with golden fixtures.
- Ensure tool names and descriptions do not regress.

### 2. Integration Tests
- Mock EMS API responses to simulate errors and retries.
- Validate token refresh and cache behavior.

### 3. Performance Tests
- Benchmark common queries for latency and payload size.

## Additional Findings from External Sources

### 1. MCP Schema Guidance (Specification Repository)
The official MCP schema emphasizes well-typed, explicit definitions for tool parameters,
resource metadata, and message content. The schema’s structure reinforces:
- **Typed payloads** for message content (text, image, audio) and a consistent envelope format.
- **Annotations** for audience, priority, and timestamps to help clients prioritize or display content.
- **Versioned schemas** to ensure compatibility and stable evolution of server capabilities.

Source: [MCP schema (JSON Schema)](https://raw.githubusercontent.com/modelcontextprotocol/specification/main/schema/2025-11-25/schema.json).

**EMS application:** Add structured metadata to responses where possible (e.g., “audience” or
priority hints for critical errors), and align tool definitions with the schema to minimize
client-side ambiguity.

### 2. OWASP Top 10 for LLM Applications (Security Risks)
OWASP’s LLM Top 10 highlights the most common, high-impact risks in AI systems:
- **Prompt Injection (LLM01):** Crafted inputs can override intent or cause data exfiltration.
- **Insecure Output Handling (LLM02):** Tool outputs should be validated before use.
- **Insecure Plugin/Tool Design (LLM07):** Untrusted inputs and weak access control can lead
  to security exploits.
- **Sensitive Information Disclosure (LLM06):** Models and tools should minimize leakage of
  secrets or personal data.

Source: [OWASP Top 10 for LLM Applications](https://genai.owasp.org/llm-top-10/).

**EMS application:** Validate all tool inputs, constrain output fields for non-privileged
users, and add server-side allowlists for potentially dangerous parameters.

### 3. NIST AI Risk Management Framework (Governance)
The NIST AI RMF emphasizes organizational governance and risk lifecycle management:
- Use structured governance to **map, measure, and manage** AI risks.
- Establish **monitoring and feedback loops** to identify emerging failure modes.
- Document risk assumptions, mitigations, and decision criteria for AI systems.

Source: [NIST AI Risk Management Framework (AI RMF 1.0)](https://doi.org/10.6028/NIST.AI.100-1).

**EMS application:** Establish a lightweight governance checklist for tool changes (schema
updates, new tools, or access scopes), and document decision logs for sensitive tools or
high-risk data exposure.

## Applying Best Practices to the EMS MCP Server

### 1. Tool Semantics
**Current Strengths**
- Tool names are action-oriented (`list_databases`, `query_database`).  
- Discovery-first workflow is already baked into the tool set.

**Recommendations**
- Add a tool-level “usage snippet” in tool descriptions to show discovery flows.
- Ensure every tool has a “default limit” when returning large datasets.

### 2. Authentication & Token Handling
**Current Strengths**
- Environment variables are used for credentials.

**Recommendations**
- Centralize token refresh logic with a lock to prevent concurrent refresh storms.
- Add a consistent retry-on-401 flow with one retry after refresh.

### 3. Pagination & Response Size
**Current Strengths**
- `query_database` accepts a `limit` parameter.

**Recommendations**
- Add pagination metadata in responses (count, next cursor, or offset).
- Offer cursors for very large datasets to minimize payload size.

### 4. Caching Strategy
**Recommendations**
- Cache:
  - EMS system list
  - Database and field hierarchies
  - Field metadata
- Apply short TTLs (15–60 minutes) and expose a cache-busting flag for debugging.

### 5. Error Messaging
**Recommendations**
- Standardize error payloads to always include:
  - error type
  - message
  - actionable hints
- Provide “next step” suggestions (e.g., “Try search_fields('altitude')”).

### 6. Observability
**Recommendations**
- Add a request ID to each tool invocation and include it in logs.
- Record timing and status per tool for latency tracking.

### 7. Documentation Improvements
**Recommendations**
- Add “Task Recipes” to `README.md` or `docs/`:
  - “Find flight data for a tail number”
  - “Explore analytics for a system”
- Add a short “common errors” section for auth, field IDs, and rate limits.

### 8. Safety & Access Control
**Recommendations**
- Validate inputs with explicit schemas (enums, ranges).
- Redact or omit sensitive fields by default unless user opts in.

## Suggested Next Steps for This Repository

1. **Add pagination metadata** to query responses, even if the EMS API is paginated only internally.
2. **Standardize error payloads** so errors are always actionable.
3. **Add cache utilities** for discovery endpoints to reduce repeated calls.
4. **Add structured logging** with a request ID and tool name.
5. **Expand documentation** with step-by-step recipes for common analytics flows.

## Reference Map (Non-Exhaustive)

Common public sources that informed the patterns above:
- MCP specification and SDK documentation (tool/resource/prompt design): https://modelcontextprotocol.io
- MCP schema (versioned JSON Schema for tool/resource definitions): https://raw.githubusercontent.com/modelcontextprotocol/specification/main/schema/2025-11-25/schema.json
- LLM integration guidance from major providers (tool safety, prompt-injection mitigation).
- API design best practices (pagination, retry logic, and error contracts).
- Observability and reliability standards from SRE and API ops guides.
- OWASP Top 10 for LLM Applications (prompt injection, insecure output handling, plugin risks): https://genai.owasp.org/llm-top-10/
- NIST AI Risk Management Framework (governance, risk lifecycle management): https://doi.org/10.6028/NIST.AI.100-1
