# Backend AI Context Map

This map exists to reduce context scanning for humans and coding agents.

## Start Here

- HTTP protocol wrapper: `erp_web/http_handler.py`
- HTTP route dispatch table: `erp_web/http_routes.py`
- HTTP route units: `erp_web/http_route_units/`
- Route-facing business facades: `erp_web/facades/`
- Runtime compatibility aggregator: `erp_web/runtime.py`
- Core shape hints: `erp_web/schemas/`

## Route Areas

- GET pages, state, static files, status APIs: `erp_web/http_route_units/get_routes.py`
- Collection and browser collection APIs: `erp_web/http_route_units/collect_routes.py`
- Copy and prompt generation APIs: `erp_web/http_route_units/copy_routes.py`
- Auth, AI config, and settings APIs: `erp_web/http_route_units/auth_config_routes.py`
- Global AI function prompts are JSON-backed through `services/ai_prompt_templates.py` and saved in `ai_use_case_prompts`; each row points to one file with `description`, `system`, and `user`. `research.web_search` is the default AI product-research template and is edited from the global function-binding settings.
- Category cache, search, suggestion, and precheck APIs: `erp_web/http_route_units/category_routes.py`
- Product save/load/delete and pricing APIs: `erp_web/http_route_units/product_routes.py`
- Product research temporary hot-product runs (`POST /api/v1/product-research/hot-products/search`) create async runs; `GET /api/v1/product-research/hot-products/runs?run_id=...` polls status, source results, and weak progress descriptions. Source registry/settings APIs and search provider test API live in `erp_web/http_route_units/product_research_routes.py`, backed by `erp_web/facades/product_research_facade.py` and `services/product_research_service.py`.
- Product research defaults and config normalization: `erp_web/product_research_config.py`; default prompt loading and template rendering use `services/ai_prompt_templates.py`; user-facing search methods live in `product_research.search_providers`. Target markets (`product_research.target_markets[]`) own their search-method bindings in `search_methods[]`, while each concrete search method implements the `ProductResearchSearchMethod.run(...) -> list[HotProductCandidate]` contract in `services/product_research_methods.py`. Hot-product candidates are runtime results from the selected search methods and are not stored in product-research settings.
- Product research AI search prompts are decoupled from search providers. The default template is `ai_use_case_prompts.research.web_search.path` such as `config/prompts/ai_example.json`; target-market generated prompts are stored per binding in `product_research.target_markets[].search_methods[].config_json.prompt` and are not shown in global function binding.
- Product research AI web search is a first-class search provider strategy (`source_type: ai_search`, `config_json.provider_strategy: ai_web_search`). It resolves a model through `services/ai_gateway.py`, can optionally pin `config_json.ai_model_id`, and streams model output tokens into the async run `description` while the run is still active.
- Product research run summaries append to `data/logs/product_research_runs.jsonl` from `services/product_research_service.py`; records include run id, target markets, source status, item count, and candidate previews. Temporary run snapshots are cached under `data/cache/product_research/runs/{run_id}.json` with `expires_at`; these cached `HotProductCandidate` rows support refresh/debug recovery and are not formal product records.
- Publish precheck, payload preview, real publish, and queue APIs: `erp_web/http_route_units/publish_routes.py`
- Mercado Libre order notification webhook: `erp_web/http_route_units/mercadolibre_routes.py` handles `POST /api/mercadolibre/notifications`; recent order pull and notification cache are exposed from `GET /api/mercadolibre/orders` in `get_routes.py`.
- Each route unit declares `HANDLED_PATHS` and a handler map (`GET_HANDLERS` or `POST_HANDLERS`) so a path can be resolved without reading the whole route file.
- Product, product research, collection, and publish routes delegate orchestration to `erp_web/facades/product_facade.py`, `erp_web/facades/product_research_facade.py`, `erp_web/facades/collect_facade.py`, and `erp_web/facades/publish_facade.py`.
- Runtime unit modules under `erp_web/runtime_units/` use explicit imports. `runtime_common.py` only holds shared constants and legacy common dependencies; do not use it as a wildcard dependency source.
- Publish and collection compatibility aggregators (`publish_runtime.py`, `source_collect.py`) use explicit export lists instead of wildcard imports.
- Static assets and auth helper pages: `routes/static_routes.py`
- Image upload and image pool API: `routes/image_routes.py`
- Product collection workflows: `erp_web/runtime_units/source_collect_workflows.py`
- Product persistence and index: `erp_web/runtime_units/product_store.py`
- Image pool pure helpers and display/read logic: `erp_web/runtime_units/image_pool_core.py`
- Image pool persistence and product mutation actions: `erp_web/runtime_units/image_pool.py`
- Copy generation: `erp_web/runtime_units/copy_generation.py`
- Category cache and suggestions: `erp_web/runtime_units/category_store.py`, `erp_web/runtime_units/category_refresh.py`
- Publish precheck and payloads: `erp_web/runtime_units/publish_validation.py`, `erp_web/runtime_units/publish_helpers.py`
- Mercado Libre publish flow: `erp_web/runtime_units/publish_mercadolibre.py`
- Mercado Libre order notifications and recent orders: `erp_web/runtime_units/mercadolibre_orders.py`
- Publish queue: `publishing_bus.py`, `erp_web/runtime_units/publish_bus.py`
- Product model units: `product_model_units/` use explicit imports; `product_model.py` remains the compatibility re-export layer.
- Marketplace publish units: `marketplace_publish_units/` use explicit imports; `marketplace_publish.py` remains the compatibility re-export layer.
- AI model config and unified gateway: `services/ai_model_config.py`, `services/ai_gateway.py`, `services/config_service.py`, `erp_web/app_config.py`

## Data Shapes

- Product and source fields: `erp_web/schemas/product.py`
- Image pool items: `erp_web/schemas/image.py`
- Publish jobs: `erp_web/schemas/publish.py`
- App/store config: `erp_web/schemas/config.py`
- Product research search providers, target markets, temporary hot-product candidates, and run results: `erp_web/schemas/product_research.py`
- API response envelope: `erp_web/schemas/api.py`

## Compatibility Notes

- `erp_web/runtime.py`, `product_model.py`, and `marketplace_publish.py` dynamically re-export functions from unit modules. Prefer reading the unit module named in this map instead of starting from the aggregator.
- `erp_web/http_handler.py` should stay thin. New API behavior should go into a focused route unit or service module, not directly into the handler class.
- Route units should use explicit imports or focused facades. Do not add new `from erp_web.runtime import *` imports; `erp_web/runtime.py` is only a compatibility aggregator.
- `erp_web/runtime_units/image_pool_core.py` is the dependency-light layer for image refs, dimensions, and pool display helpers. Product storage and publish modules should depend on it instead of importing `image_pool.py`.
- `tests/test_ai_context_architecture.py` locks the no-wildcard rule for route/facade layers, product/marketplace units, and runtime unit modules.
