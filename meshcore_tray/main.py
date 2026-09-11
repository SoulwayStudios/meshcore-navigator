"""Application Entry Point for Heltec V3 MeshCore System Tray & Pixoo 64 Integration."""

import argparse
import asyncio
import logging
import sys
from PyQt6.QtWidgets import QApplication
import qasync

from meshcore_tray.config import AppConfig
from meshcore_tray.core.event_bus import bus, EventType
from meshcore_tray.core.gateway import GatewayManager
from meshcore_tray.core.mention_detector import MentionDetector
from meshcore_tray.drivers.meshcore_driver import MeshCoreDriver
from meshcore_tray.drivers.mock_driver import MockRadioDriver
from meshcore_tray.pixoo.pixoo_service import PixooService
from meshcore_tray.storage import Storage
from meshcore_tray.ui.main_window import MainWindow
from meshcore_tray.ui.styles import DARK_THEME_QSS
from meshcore_tray.ui.tray import SystemTray

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("meshcore_tray.main")


async def async_main(args, storage_holder: dict):
    # 1. Load Configuration & Storage
    config = AppConfig.load()
    storage = Storage()
    storage_holder["storage"] = storage

    # Run startup verification & auto-sanitization
    sanitize_report = storage.verify_and_sanitize_database()
    if (
        sanitize_report.get("corrupt_coords_cleared", 0) > 0
        or sanitize_report.get("phantom_nodes_removed", 0) > 0
        or sanitize_report.get("future_timestamps_fixed", 0) > 0
    ):
        logger.info(f"Startup database verification & sanitization report: {sanitize_report}")


    if args.mock:
        config.meshcore.simulation_mode = True
        config.meshcore.connection_type = "mock"
    else:
        ports = MeshCoreDriver.scan_serial_ports()
        if ports:
            config.meshcore.simulation_mode = False
            if config.meshcore.connection_type == "mock":
                config.meshcore.connection_type = "serial"
        else:
            logger.info("No physical serial radio detected; running simulator.")
            config.meshcore.simulation_mode = True

    # 2. Mention & Keyword Detector
    detector = MentionDetector(config=config)
    bus.subscribe(EventType.MESSAGE_RECEIVED, detector.evaluate_message)

    # 3. Radio Driver Selection
    if config.meshcore.simulation_mode or config.meshcore.connection_type == "mock":
        radio_driver = MockRadioDriver(config=config, storage=storage)
    else:
        radio_driver = MeshCoreDriver(config=config, storage=storage)

    # 4. Pixoo Display & Animation Service
    pixoo_service = PixooService(config=config)

    # 5. Extensibility Gateway (Local HTTP Bridge)
    gateway = GatewayManager(config=config, storage=storage, radio_driver=radio_driver)
    if config.gateway.http_bridge_enabled:
        gateway.start_http_bridge(config.gateway.http_port)

    # 6. UI Creation
    main_window = MainWindow(
        config=config,
        storage=storage,
        radio_driver=radio_driver,
        pixoo_service=pixoo_service
    )
    storage_holder["main_window"] = main_window
    tray = SystemTray(main_window=main_window, config=config)
    tray.show()

    # Headless test-init exit
    if args.test_init:
        logger.info("Headless initialization check succeeded.")
        return 0

    # Start background services
    asyncio.create_task(radio_driver.start())
    asyncio.create_task(pixoo_service.start())

    # Show main window on start
    main_window.show()

    return 0


def main():
    parser = argparse.ArgumentParser(description="MeshCore Map Mixer")
    parser.add_argument("--test-init", action="store_true", help="Run initialization test and exit")
    parser.add_argument("--mock", action="store_true", help="Force Mock Radio Driver simulation mode")
    parser.add_argument("--debug", action="store_true", help="Enable debug logging")
    args = parser.parse_args()

    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)

    app = QApplication(sys.argv)
    app.setApplicationName("MESHCORE NAVIGATOR")
    app.setQuitOnLastWindowClosed(False)
    app.setStyleSheet(DARK_THEME_QSS)

    from meshcore_tray.ui.crash_dialog import install_crash_handler
    install_crash_handler()

    loop = qasync.QEventLoop(app)
    asyncio.set_event_loop(loop)
    bus.set_loop(loop)

    storage_holder = {}

    def _safe_quit():
        st = storage_holder.get("storage")
        if st:
            try:
                st.backup_database(reason="app_quit")
            except Exception as e:
                logger.warning(f"Error creating database backup on quit: {e}")
        mw = storage_holder.get("main_window")
        if mw and hasattr(mw, "cleanup"):
            try:
                mw.cleanup()
            except Exception as e:
                logger.debug(f"Cleanup note: {e}")
        try:
            if loop.is_running():
                loop.stop()
        except Exception:
            pass

    app.aboutToQuit.connect(_safe_quit)

    with loop:
        try:
            if args.test_init:
                ret = loop.run_until_complete(async_main(args, storage_holder))
                sys.exit(ret)
            else:
                loop.create_task(async_main(args, storage_holder))
                loop.run_forever()

        except (KeyboardInterrupt, SystemExit):
            pass
        except RuntimeError as e:
            if "Event loop stopped" not in str(e):
                raise
        finally:
            try:
                app.processEvents()
            except Exception:
                pass


if __name__ == "__main__":
    main()
