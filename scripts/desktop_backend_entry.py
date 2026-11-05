"""PyInstaller 的薄入口，业务服务仍由 erp_web.server 持有。"""

from erp_web.desktop import main

if __name__ == "__main__":
    main()
