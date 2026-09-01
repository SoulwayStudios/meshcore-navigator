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


async def async_main(args):
    # 1. Load Configuration & Storage
    config = AppConfig.load()
    storage = Storage()

    if args.mock:
        config.meshcore.simulation_mode = True

    # 2. Mention & Keyword Detector
    detector = MentionDetector(config=config)
    bus.subscribe(EventType.MESSAGE_RECEIVED, detector.evaluate_message)

    # 3. Radio Driver Selection
    if config.meshcore.simulation_mode:
        radio_driver = MockRadioDriver(config=config, storage=storage)
    else:
        ports = MeshCoreDriver.scan_serial_ports()
        if not ports:
            logger.info("No physical serial radio found, initializing in simulation mode.")
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
    parser = argparse.ArgumentParser(description="MeshCore & Pixoo 64 System Tray Application")
    parser.add_argument("--test-init", action="store_true", help="Run initialization test and exit")
    parser.add_argument("--mock", action="store_true", help="Force Mock Radio Driver simulation mode")
    parser.add_argument("--debug", action="store_true", help="Enable debug logging")
    args = parser.parse_args()

    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)

    app = QApplication(sys.argv)
    app.setApplicationName("MeshCore Pixoo Tray")
    app.setQuitOnLastWindowClosed(False)
    app.setStyleSheet(DARK_THEME_QSS)

    loop = qasync.QEventLoop(app)
    asyncio.set_event_loop(loop)
    bus.set_loop(loop)

    app.aboutToQuit.connect(loop.stop)

    with loop:
        try:
            if args.test_init:
                sys.exit(loop.run_until_complete(async_main(args)))
            else:
                loop.create_task(async_main(args))
                loop.run_forever()
        except (KeyboardInterrupt, SystemExit):
            sys.exit(0)
        except RuntimeError as e:
            if "Event loop stopped" not in str(e):
                raise
            sys.exit(0)


if __name__ == "__main__":
    main()
