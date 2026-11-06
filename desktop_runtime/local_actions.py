"""固定的本机文件夹操作。"""
import os
import subprocess
import sys

def open_profile(config):
    config.browser_profile.mkdir(parents=True, exist_ok=True)
    if os.name == 'nt':
        os.startfile(str(config.browser_profile))
    else:
        subprocess.Popen(['open' if sys.platform == 'darwin' else 'xdg-open', str(config.browser_profile)])
    return {'ok': True, 'profile_dir': str(config.browser_profile)}


