# -*- coding: utf-8 -*-
from __future__ import annotations

import sys

from erp_web import runtime as _runtime
from erp_web.server import main

_runtime.main = main

if __name__ == "__main__":
    main()
else:
    sys.modules[__name__] = _runtime
