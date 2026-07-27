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
from erp_web.context import get_context
from erp_web.runtime_units.publishing_bus_core import PublishingBus


# 过渡：路径/端口常量现在由 AppPaths 派生（erp_web/context.py），此处仅保留
# 模块级名字向后兼容（含测试 monkeypatch）。新代码请用 get_context().paths。
_paths = get_context().paths
APP_DIR = _paths.app_dir
DIST_DIR = _paths.dist_dir
DATA_DIR = _paths.data_dir
CONFIG_DIR = _paths.config_dir
CACHE_DIR = _paths.cache_dir
LOGS_DIR = _paths.logs_dir
IMAGES_DIR = _paths.images_dir
EXPORTS_DIR = _paths.exports_dir
OUTPUT_DIR = _paths.output_dir
STORE_CONFIG_PATH = _paths.store_config_path
APP_CONFIG_PATH = _paths.app_config_path
REMOVED_LEGACY_CONFIG_PATHS: tuple[Path, ...] = _paths.removed_legacy_config_paths
LEGACY_STORE_CONFIG_PATHS = _paths.legacy_store_config_paths
LEGACY_APP_CONFIG_PATHS = _paths.legacy_app_config_paths
TASK_DIR = _paths.task_dir
CHATGPT_DIR = _paths.chatgpt_dir
SOURCE_DIR = _paths.source_dir
UPLOAD_DIR = _paths.upload_dir
COLLECT_DEBUG_DIR = _paths.collect_debug_dir
BROWSER_PROFILE_DIR = _paths.browser_profile_dir
FRONT_DIR = _paths.front_dir
FRONT_DIST_DIR = _paths.front_dist_dir
FRONT_DIST_INDEX_PATH = _paths.front_dist_index_path
WEB_TEMPLATE_PATH = _paths.web_template_path
# Port convention: see AppPaths.from_app_dir (ERP_PORT is the single knob).
WEB_PORT = _paths.web_port
BROWSER_DEBUG_PORT = _paths.browser_debug_port
DEFAULT_EXCHANGE_RATE_API_URL = "https://open.er-api.com/v6/latest/USD"
AI_TEXT_REQUEST_TIMEOUT_SECONDS = int(os.environ.get("AI_TEXT_REQUEST_TIMEOUT_SECONDS", "60"))
AI_IMAGE_REQUEST_TIMEOUT_SECONDS = int(os.environ.get("AI_IMAGE_REQUEST_TIMEOUT_SECONDS", "180"))
BROWSER_DEBUG_PROFILE_DIR = _paths.browser_debug_profile_dir
DRAFT_WORKFLOW_STATUSES = (
    "collected",
    "claimed",
    "copy_ready",
    "images_ready",
    "ready_to_publish",
    "published",
)

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
