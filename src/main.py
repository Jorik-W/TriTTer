#!/usr/bin/env python3
"""TriTTer - unified cycling toolkit (Analyze CdA + Plan pacing).

Entry point. Launches the three-tab GUI by default, or a CLI for either mode.

Usage:
    python main.py                      # GUI
    python main.py --gui                # GUI (explicit)
    python main.py --cli                # CLI, Analyze mode (default)
    python main.py --cli --mode analyze --file <ride.fit>
    python main.py --cli --mode plan    # Plan CLI (next phase)
"""

import os
import sys
import argparse

# --- Make the package layout importable with the existing flat imports -----
_SRC = os.path.dirname(os.path.abspath(__file__))
for _sub in ("core", "analyze", "plan", "ui"):
    _p = os.path.join(_SRC, _sub)
    if _p not in sys.path:
        sys.path.insert(0, _p)
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)


def _run_gui(argv):
    from PyQt5.QtWidgets import QApplication
    from PyQt5.QtCore import QTimer, Qt, QCoreApplication
    from app_shell import TriTTerWindow

    # QtWebEngine (folium map view) requires shared GL contexts set before the
    # QApplication is constructed.
    QCoreApplication.setAttribute(Qt.AA_ShareOpenGLContexts)

    # Reuse the analyzer's crash reporting / splash if available.
    try:
        from qt_gui import (
            _install_global_error_reporting, create_splash, resource_path,
        )
    except Exception:
        _install_global_error_reporting = None
        create_splash = None
        resource_path = None

    app = QApplication(sys.argv)
    try:
        from theme import apply_theme
        apply_theme(app)
    except Exception:
        pass
    if _install_global_error_reporting is not None:
        try:
            _install_global_error_reporting(app, enable_file_log=False, crash_log_path=None)
        except Exception:
            pass

    splash = None
    if create_splash is not None and resource_path is not None:
        try:
            splash = create_splash(app, resource_path("icons/logo.PNG"),
                                   "Loading TriTTer...")
        except Exception:
            splash = None

    def create_main_window():
        if splash:
            splash.close()
        app.main_window = TriTTerWindow(app)
        app.main_window.show()

    if splash is not None:
        QTimer.singleShot(1500, create_main_window)
    else:
        create_main_window()

    sys.exit(app.exec_())


def _run_cli(mode, remaining):
    if mode == "plan":
        print("Plan CLI is being ported in the next phase. Use --mode analyze for now.")
        return 0
    # Analyze CLI (cda_analyzer)
    from cli import main as analyze_cli_main
    sys.argv = [sys.argv[0]] + remaining
    analyze_cli_main()
    return 0


def main():
    parser = argparse.ArgumentParser(description="TriTTer", add_help=False)
    parser.add_argument("--gui", action="store_true", help="Launch GUI (default)")
    parser.add_argument("--cli", action="store_true", help="Launch CLI")
    parser.add_argument("--mode", choices=("analyze", "plan"), default="analyze",
                        help="CLI mode (default: analyze)")
    args, remaining = parser.parse_known_args()

    if args.cli:
        sys.exit(_run_cli(args.mode, remaining))
    else:
        _run_gui(remaining)


if __name__ == "__main__":
    main()
