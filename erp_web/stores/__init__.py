# -*- coding: utf-8 -*-
"""Domain stores owned by :class:`erp_web.context.AppContext`.

Each store receives its dependencies (``ErpDatabase`` / ``AppPaths``) through
the constructor and never touches process globals. Callers import these
owners directly; no runtime compatibility forwarding layer is kept.
"""
