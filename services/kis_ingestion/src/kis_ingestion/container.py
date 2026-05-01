import httpx
from pathlib import Path
from kis_ingestion.config import KISConfig
from kis_ingestion.token_manager import KISTokenManager
from kis_ingestion.approval_key_manager import KISApprovalKeyManager
from kis_ingestion.ws_client import KISWebSocketClient
from kis_ingestion.subscription_pool import KISSubscriptionPool
from kis_ingestion.market_session import MarketSessionRouter, ScheduleBasedSessionSwitcher
from kis_ingestion.raw_parser import KISRawMessageParser
from kis_ingestion.tick_parser import KISTickParser
from kis_ingestion.connection_manager import KISConnectionManager
from .producer import StockTickProducer

def create_container(config: KISConfig) -> tuple[KISConnectionManager, KISSubscriptionPool, httpx.AsyncClient, StockTickProducer | None]:
    """
    Wire the object graph. Returns (connection_manager, subscription_pool, http_client, producer).
    Caller is responsible for closing http_client.
    """
    http_client = httpx.AsyncClient()
    
    # token_manager is wired for future use (REST API calls)
    _token_manager = KISTokenManager(
        base_url=config.token_url.rsplit("/oauth2/tokenP", 1)[0],
        app_key=config.app_key,
        app_secret=config.app_secret,
        http_client=http_client,
    )
    
    approval_key_manager = KISApprovalKeyManager(
        approval_url=config.approval_url,
        app_key=config.app_key,
        app_secret=config.app_secret,
        http_client=http_client,
    )
    
    ws_client = KISWebSocketClient(ws_url=config.ws_url)
    
    subscription_pool = KISSubscriptionPool(cap=config.subscription_cap)
    
    switcher = ScheduleBasedSessionSwitcher()
    initial_adapter = switcher.determine_initial_market()
    market_router = MarketSessionRouter(initial_adapter)
    
    raw_parser = KISRawMessageParser()
    tick_parser = KISTickParser()
    
    # Kafka producer
    producer: StockTickProducer | None = None
    if config.kafka_enabled:
        schema_path = str(Path(__file__).resolve().parent.parent.parent.parent.parent / "schemas" / "stock-ticks.avsc")
        producer = StockTickProducer(
            bootstrap_servers=config.kafka_bootstrap_servers,
            topic=config.kafka_topic,
            schema_path=schema_path,
        )
    
    connection_manager = KISConnectionManager(
        approval_key_manager=approval_key_manager,
        ws_client=ws_client,
        subscription_pool=subscription_pool,
        raw_parser=raw_parser,
        tick_parser=tick_parser,
        market_router=market_router,
        producer=producer,
    )
    
    return connection_manager, subscription_pool, http_client, producer
