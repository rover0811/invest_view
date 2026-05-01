import asyncio
import logging
import signal
import sys

from kis_ingestion.config import KISConfig
from kis_ingestion.container import create_container

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

async def main() -> None:
    try:
        config = KISConfig()
    except Exception as e:
        logger.error("Failed to load config: %s", e)
        logger.error("Set KIS_APP_KEY and KIS_APP_SECRET environment variables")
        sys.exit(1)
    
    connection_manager, subscription_pool, http_client = create_container(config)
    
    # Set desired subscriptions from config
    if config.watch_symbols:
        # The market router is inside connection_manager, use its tick_tr_id
        # Accessing protected member for initialization in entrypoint
        tr_id = connection_manager._market_router.tick_tr_id
        subscription_pool.set_desired(config.watch_symbols, tr_id)
    
    loop = asyncio.get_running_loop()
    
    async def shutdown():
        logger.info("Shutting down...")
        await connection_manager.stop()
        await http_client.aclose()
    
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, lambda: asyncio.create_task(shutdown()))
        except NotImplementedError:
            # signal.add_signal_handler is not implemented on Windows
            pass
    
    try:
        logger.info("Starting KIS ingestion service...")
        # Accessing protected member for logging in entrypoint
        logger.info("Market: %s, Symbols: %s", connection_manager._market_router.market_name, config.watch_symbols)
        await connection_manager.start()
    except Exception:
        logger.exception("KIS ingestion service failed")
    finally:
        await http_client.aclose()

if __name__ == "__main__":
    asyncio.run(main())
