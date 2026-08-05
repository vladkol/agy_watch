"""Cross-platform system clipboard integration for agy_watch."""

import os
import subprocess
import sys


def copy_to_system_clipboard(text: str) -> bool:
    """Writes text directly to the native OS system clipboard across macOS, Linux, and Windows.

    Supports:
    - macOS: pbcopy
    - Linux (Wayland): wl-copy
    - Linux (X11): xclip / xsel
    - Windows: clip.exe
    """
    if not text:
        return False

    # 1. macOS (pbcopy)
    if sys.platform == 'darwin':
        try:
            p = subprocess.Popen(['pbcopy'], stdin=subprocess.PIPE, close_fds=True)
            p.communicate(text.encode('utf-8'))
            if p.returncode == 0:
                return True
        except Exception:
            pass

    # 2. Linux Wayland (wl-copy)
    if sys.platform.startswith('linux'):
        if os.environ.get('WAYLAND_DISPLAY'):
            try:
                p = subprocess.Popen(['wl-copy'], stdin=subprocess.PIPE, close_fds=True)
                p.communicate(text.encode('utf-8'))
                if p.returncode == 0:
                    return True
            except Exception:
                pass

        # 3. Linux X11 (xclip / xsel)
        try:
            p = subprocess.Popen(['xclip', '-selection', 'clipboard'], stdin=subprocess.PIPE, close_fds=True)
            p.communicate(text.encode('utf-8'))
            if p.returncode == 0:
                return True
        except Exception:
            pass

        try:
            p = subprocess.Popen(['xsel', '--clipboard', '--input'], stdin=subprocess.PIPE, close_fds=True)
            p.communicate(text.encode('utf-8'))
            if p.returncode == 0:
                return True
        except Exception:
            pass

    # 4. Windows (clip.exe)
    if sys.platform in ('win32', 'cygwin'):
        try:
            p = subprocess.Popen(['clip.exe'], stdin=subprocess.PIPE, close_fds=True)
            p.communicate(text.encode('utf-8'))
            if p.returncode == 0:
                return True
        except Exception:
            pass

    return False
