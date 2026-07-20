# -*- coding: utf-8 -*-
from __future__ import annotations

import base64
import json
import os
import re
import shutil
import sys
from copy import deepcopy
import subprocess
import socket
import struct
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import webbrowser
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from typing import Any

from erp_web import db as erp_db
from erp_web import listing_planner as generator
from erp_web import marketplaces as publisher
from erp_web import app_config as app_config_runtime
from erp_web.services import collect_service, config_service, copy_service, html_extract_service as legacy, image_service, pricing_service
from erp_web.product_model import (
    apply_ai_attribute_fill,
    apply_category_selection,
    default_collect_diagnostics,
    default_draft,
    default_product_model,
    PLATFORMS,
    image_pool_legacy_views,
    normalize_image_pool_item,
    normalize_platforms,
    merge_source_partial_result,
    normalize_image_pool,
    normalize_product_model,
    parse_dimensions_text,
    SOURCE_COMPAT_IMAGE_ORIGINS,
    validate_category_precheck,
)
from erp_web.runtime_units.publishing_bus_core import PublishingBus


APP_DIR = Path(__file__).resolve().parents[2]
DIST_DIR = APP_DIR / "dist"
DATA_DIR = APP_DIR / "data"
CONFIG_DIR = APP_DIR / "config"
CACHE_DIR = DATA_DIR / "cache"
LOGS_DIR = DATA_DIR / "logs"
IMAGES_DIR = DATA_DIR / "images"
EXPORTS_DIR = DATA_DIR / "exports"
OUTPUT_DIR = LOGS_DIR
STORE_CONFIG_PATH = CONFIG_DIR / "store_config.json"
APP_CONFIG_PATH = CONFIG_DIR / "app_config.json"
REMOVED_LEGACY_CONFIG_PATHS: tuple[Path, ...] = ()
LEGACY_STORE_CONFIG_PATHS = REMOVED_LEGACY_CONFIG_PATHS
LEGACY_APP_CONFIG_PATHS = REMOVED_LEGACY_CONFIG_PATHS
PUBLISH_LOG_PATH = OUTPUT_DIR / "publish_logs.json"
PUBLISHING_JOB_DIR = OUTPUT_DIR / "publishing_jobs"
TASK_DIR = OUTPUT_DIR / "codex_tasks"
CHATGPT_DIR = IMAGES_DIR / "chatgpt"
SOURCE_DIR = IMAGES_DIR / "source"
UPLOAD_DIR = IMAGES_DIR / "uploads"
COLLECT_DEBUG_DIR = CACHE_DIR / "collect_debug"
BROWSER_PROFILE_DIR = APP_DIR / "browser_profile" / "1688"
FRONT_DIR = APP_DIR / "front"
FRONT_DIST_DIR = APP_DIR / "erp_web" / "static" / "dist"
FRONT_DIST_INDEX_PATH = FRONT_DIST_DIR / "index.html"
WEB_TEMPLATE_PATH = FRONT_DIR / "index.html"
WEB_PORT = int(os.environ.get("ERP_PORT", "5000"))
BROWSER_DEBUG_PORT = int(os.environ.get("ERP_BROWSER_DEBUG_PORT", "9222"))
DEFAULT_EXCHANGE_RATE_API_URL = "https://open.er-api.com/v6/latest/USD"
AI_TEXT_REQUEST_TIMEOUT_SECONDS = int(os.environ.get("AI_TEXT_REQUEST_TIMEOUT_SECONDS", "60"))
AI_IMAGE_REQUEST_TIMEOUT_SECONDS = int(os.environ.get("AI_IMAGE_REQUEST_TIMEOUT_SECONDS", "180"))
BROWSER_DEBUG_PROFILE_DIR = Path(os.environ.get("ERP_BROWSER_PROFILE_DIR", str(APP_DIR / "browser_profile" / "debug")))
DRAFT_WORKFLOW_STATUSES = (
    "collected",
    "claimed",
    "copy_ready",
    "images_ready",
    "ready_to_publish",
    "published",
)
EXCHANGE_RATE_CACHE: dict[str, Any] = {}

VERIFY_MARKERS = (
    "安全验证",
    "slide.1688.com",
    "请验证身份",
    "验证码",
    "captcha",
    "verify",
    "security verification",
)

AMAZON_VERIFY_MARKERS = (
    "robot check",
    "captcha",
    "enter the characters you see below",
    "validatecaptcha",
    "sorry, this page is not available",
    "this item is no longer available",
)

__all__ = [name for name in globals() if not name.startswith("__")]
