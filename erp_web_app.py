# -*- coding: utf-8 -*-
from __future__ import annotations

import sys

from erp_web import runtime as _runtime
from erp_web.server import main

_runtime.main = main
FRONT_DIST_INDEX_PATH = _runtime.FRONT_DIST_INDEX_PATH
FRONT_DIST_DIR = _runtime.FRONT_DIST_DIR

# Static assets are still served by erp_web.http_handler when parsed.path.startswith("/assets/").

if __name__ == "__main__":
    main()
else:
    sys.modules[__name__] = _runtime
