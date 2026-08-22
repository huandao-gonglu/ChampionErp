"""Section 9 auditable data repair for product a296370a0f9db8ee / draft d2b4e9f048b92.

Uses the repaired focused Capabilities (product_profile_patch, draft_stock_update,
draft_pricing_apply, product_attributes_update, upc_assign, product_publish_validate)
via the real AiToolRuntime + Task ToolSet. Does NOT edit SQLite JSON directly.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("ERP_APP_DIR", ".")

from datetime import datetime, timedelta, timezone

from erp_web.context import get_context
from erp_web.facades import global_task_facade
from erp_web.schemas.ai_tools import AiToolCommand
from erp_web.schemas.ai_trace import AiExecutionContext
from erp_web.services.ai_tool_runtime import AiToolRuntime

PRODUCT_ID = "a296370a0f9db8ee"
DRAFT_ID = "d2b4e9f048b92"


def _execution(operation_key: str) -> AiExecutionContext:
    return AiExecutionContext.create(
        timeout_seconds=120,
        budget_profile="data.repair",
        task_run_id="data-repair",
        attempt_id=f"data-repair:{operation_key}",
        permissions=global_task_facade.application_capability_permissions(),
        business_scope={"task_id": "data-repair", "step_id": operation_key},
        idempotency_context={"operation_key": operation_key},
        allow_write=True,
    )


def main() -> int:
    context = get_context()
    scope = global_task_facade.build_capability_binding_scope(context)
    toolset = global_task_facade.bind_global_task_toolset(
        scope=scope,
        declared_permissions=global_task_facade.application_capability_permissions(),
    )

    def run(name: str, arguments: dict, op: str) -> dict:
        runtime = AiToolRuntime(
            toolset=toolset,
            execution_context=_execution(op),
            max_tool_calls=1,
        )
        result = runtime.execute(
            AiToolCommand(
                call_id=f"data-repair-{op}",
                tool_name=name,
                tool_version="1",
                arguments=arguments,
                round=1,
            )
        )
        payload = result.to_dict()
        print(f"[{name}] ok={result.ok}")
        if not result.ok:
            print("  error:", payload.get("error"))
        else:
            print("  output:", payload.get("output"))
        return payload

    # 1. Restore product master from source facts (partial patch; other fields kept).
    run(
        "product_profile_patch",
        {
            "product": {
                "product_id": PRODUCT_ID,
                "brand": "金诚海蓝",
                "model": "bxt-cq2",
                "cost": "9",
                "weight_kg": "0.04",
            }
        },
        "repair-1-profile",
    )

    # 2. Confirm Ozon draft stock is 10 (user's last explicit input; NOT master 200).
    run(
        "draft_stock_update",
        {"draft_id": DRAFT_ID, "stock": "10"},
        "repair-2-stock",
    )

    # 3. Apply final price 200 CNY via manual pricing (persists pricing.targets).
    run(
        "draft_pricing_apply",
        {
            "draft_id": DRAFT_ID,
            "target_platform": "ozon",
            "site": "global",
            "pricing_input": {
                "target": {
                    "pricing_mode": "manual",
                    "manual_price": {"amount": "200", "currency": "CNY"},
                    "shipping_quote_mode": "manual",
                    "shipping_currency": "CNY",
                    "shipping_amount": "10",
                }
            },
        },
        "repair-3-pricing",
    )

    # 4. Resolve attribute 85 -> Нет бренда (user-confirmed), keep master brand fact.
    run(
        "product_attributes_update",
        {
            "draft_id": DRAFT_ID,
            "platform": "ozon",
            "site": "global",
            "updates": {
                "85": {
                    "values": [
                        {
                            "dictionary_value_id": "126745801",
                            "value": "Нет бренда",
                        }
                    ]
                }
            },
        },
        "repair-4-attributes",
    )

    # 5. Assign a UPC from the pool to the product.
    run("upc_assign", {"product_id": PRODUCT_ID}, "repair-5-upc")

    # 6. Re-run publish validate for the readiness conclusion.
    validate = run(
        "product_publish_validate",
        {"draft_id": DRAFT_ID, "platform": "ozon", "site": "global"},
        "repair-6-validate",
    )

    print("\n=== final state ===")
    p = context.products.load_product_from_index(PRODUCT_ID, "")
    for k in ("brand", "model", "cost", "weight_kg", "stock", "upc"):
        print(f"  product.{k} =", repr(p.get(k)))
    d = context.db.load_draft_model(DRAFT_ID)
    for k in ("stock", "upc", "status", "publish_status"):
        print(f"  draft.{k} =", repr(d.get(k)))
    print("  draft.pricing.targets keys =", list((d.get("pricing") or {}).get("targets", {})))
    print("  draft.validation_errors =", repr(d.get("validation_errors")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
