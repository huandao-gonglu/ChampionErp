# -*- coding: utf-8 -*-
"""Domain stores owned by :class:`erp_web.context.AppContext`.

Each store receives its dependencies (``ErpDatabase`` / ``AppPaths``) through
the constructor and never touches process globals; ``get_context()`` is the
single place that wires them up. The thin functions in
``erp_web.runtime_units.product_store`` merely delegate here.
"""
