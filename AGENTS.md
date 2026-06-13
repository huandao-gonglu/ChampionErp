# AGENTS.md

本项目的后续设计和重构，默认以“AI 能低成本检索上下文”为架构目标之一。新增功能不能只追求能跑，还要让后续 AI 和人类能快速定位入口、依赖、数据形状和测试边界。

<!-- CODEGRAPH_START -->
## CodeGraph

This project has a CodeGraph MCP server (`codegraph_*` tools) configured. CodeGraph is a tree-sitter-parsed knowledge graph of every symbol, edge, and file. Reads are sub-millisecond and return structural information grep cannot.

### When to prefer codegraph over native search

Use codegraph for **structural** questions: what calls what, what would break, where X is defined, what X's signature is. Use native grep/read only for **literal text** queries such as strings, comments, log messages, or after you already have a specific file open.

| Question | Tool |
|---|---|
| "Where is X defined?" / "Find symbol named X" | `codegraph_search` |
| "What calls function Y?" | `codegraph_callers` |
| "What does Y call?" | `codegraph_callees` |
| "How does X reach/become Y?" | `codegraph_trace` |
| "What would break if I changed Z?" | `codegraph_impact` |
| "Show me Y's signature / source / docstring" | `codegraph_node` |
| "Give me focused context for a task/area" | `codegraph_context` |
| "See several related symbols' source at once" | `codegraph_explore` |
| "What files exist under path/" | `codegraph_files` |
| "Is the index healthy?" | `codegraph_status` |

### Rules of thumb

- For architecture or flow questions, start with `codegraph_context` or `codegraph_trace` instead of grep/read loops.
- Trust CodeGraph for AST-level structure. Do not re-check symbol locations with grep unless searching literal text.
- If a response says files are pending re-index, read only those listed files for fresh content.
- If `.codegraph/` is missing, ask before running `codegraph init -i`.
<!-- CODEGRAPH_END -->

## AI Context Design Rules

### Entry Points

- Keep `docs/ai-context-map.md` current whenever backend areas move, split, or gain new public entry points.
- New HTTP behavior should start in `erp_web/http_route_units/` with `HANDLED_PATHS` and explicit handler maps.
- Route code should stay thin. Put orchestration in focused facades under `erp_web/facades/` or focused runtime/service modules.
- Compatibility aggregators such as `erp_web/runtime.py`, `product_model.py`, and `marketplace_publish.py` are not primary reading entry points.

### Imports And Boundaries

- Do not add new wildcard imports (`import *`) in project code.
- Do not add new `from erp_web.runtime import *` imports. Import the specific unit, facade, or service instead.
- Prefer explicit imports over runtime namespace injection.
- Public re-export modules must define `__all__`.
- Avoid module cycles. If two modules need the same helper, extract a dependency-light core module instead of using local imports to hide the cycle.
- Keep pure helpers separate from persistence, network calls, browser automation, and publish side effects.

### Current Backend Layering

- `erp_web/runtime_units/image_pool_core.py`: dependency-light image ref, display, dimensions, and pool selection helpers.
- `erp_web/runtime_units/image_pool.py`: image pool persistence and product mutation actions.
- `erp_web/runtime_units/product_store.py`: product normalization, persistence, index, config, and store auth helpers.
- `erp_web/runtime_units/source_collect_workflows.py`: source collection workflows.
- `erp_web/runtime_units/publish_helpers.py`, `publish_validation.py`, `publish_mercadolibre.py`, `publish_bus.py`: publish payload, validation, Mercado Libre publishing, and queue/log flow.

### Data Shapes

- Prefer shared shape hints in `erp_web/schemas/` for API payloads and product/image/publish/config structures.
- When adding or changing response shape, update the relevant schema/documentation before spreading ad hoc dictionaries across handlers.
- Keep compatibility fields only at boundaries. Internal code should prefer normalized product/image/publish shapes.

### Tests As Architecture Guards

- Update `tests/test_ai_context_architecture.py` when adding a new architectural rule.
- Run the backend tests after refactors:

```bash
/Users/hdgl/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 -m pytest tests -q
```

- At minimum, refactors that move imports or module boundaries must pass import/compile checks plus the architecture test.

### Refactor Style

- Prefer small files with obvious ownership over large modules with mixed concerns.
- Prefer stable dispatch tables, facades, and typed schemas over dynamic aggregators or implicit globals.
- Keep old compatibility surfaces working, but do not use them as the model for new code.
- If a change makes AI read several unrelated files to understand one behavior, split the behavior or document the intended entry point.
