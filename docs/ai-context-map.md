# Backend AI Context Map

This map exists to reduce context scanning for humans and coding agents.

## Start Here

- HTTP protocol wrapper: `erp_web/http_handler.py`
- HTTP route dispatch table: `erp_web/http_routes.py`
- HTTP route units: `erp_web/http_route_units/`
- Runtime compatibility aggregator: `erp_web/runtime.py`
- Core shape hints: `erp_web/schemas/`

## Route Areas

- GET pages, state, static files, status APIs: `erp_web/http_route_units/get_routes.py`
- Collection and browser collection APIs: `erp_web/http_route_units/collect_routes.py`
- Copy and prompt generation APIs: `erp_web/http_route_units/copy_routes.py`
- Auth, AI config, and settings APIs: `erp_web/http_route_units/auth_config_routes.py`
- Category cache, search, suggestion, and precheck APIs: `erp_web/http_route_units/category_routes.py`
- Product save/load/delete and pricing APIs: `erp_web/http_route_units/product_routes.py`
- Publish precheck, payload preview, real publish, and queue APIs: `erp_web/http_route_units/publish_routes.py`
- Static assets and auth helper pages: `routes/static_routes.py`
- Image upload and image pool API: `routes/image_routes.py`
- Product collection workflows: `erp_web/runtime_units/source_collect_workflows.py`
- Product persistence and index: `erp_web/runtime_units/product_store.py`
- Image pool runtime state: `erp_web/runtime_units/image_pool.py`
- Copy generation: `erp_web/runtime_units/copy_generation.py`
- Category cache and suggestions: `erp_web/runtime_units/category_store.py`, `erp_web/runtime_units/category_refresh.py`
- Publish precheck and payloads: `erp_web/runtime_units/publish_validation.py`, `erp_web/runtime_units/publish_helpers.py`
- Mercado Libre publish flow: `erp_web/runtime_units/publish_mercadolibre.py`
- Publish queue: `publishing_bus.py`, `erp_web/runtime_units/publish_bus.py`
- AI/provider config: `services/config_service.py`, `erp_web/app_config.py`

## Data Shapes

- Product and source fields: `erp_web/schemas/product.py`
- Image pool items: `erp_web/schemas/image.py`
- Publish jobs: `erp_web/schemas/publish.py`
- App/store config: `erp_web/schemas/config.py`
- API response envelope: `erp_web/schemas/api.py`

## Compatibility Notes

- `erp_web/runtime.py`, `product_model.py`, and `marketplace_publish.py` dynamically re-export functions from unit modules. Prefer reading the unit module named in this map instead of starting from the aggregator.
- `erp_web/http_handler.py` should stay thin. New API behavior should go into a focused route unit or service module, not directly into the handler class.
- Route units still mirror legacy behavior and import runtime globals. Future refactors should replace those calls with explicit imports one route area at a time.
