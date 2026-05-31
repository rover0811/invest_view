# Alert Service

Stock alerts serving service (Kafka consumer + WebSocket + REST API).

Kafka의 `stock-alerts` 토픽을 구독하여 수신된 알림 이벤트를 PostgreSQL(`alert_service.alert_events`)에 저장하고, FastAPI를 통해 외부 인터페이스를 제공하는 서비스입니다. Docker 데몬으로 실행됩니다.

## 환경 설정
- **데이터베이스**: `ALERT_SERVICE_DATABASE_URL` (postgresql+asyncpg://...)
- **Kafka**: `ALERT_SERVICE_KAFKA_BOOTSTRAP_SERVERS=kafka:29092`, `ALERT_SERVICE_SCHEMA_REGISTRY_URL`
- **Avro**: `ALERT_SERVICE_AVRO_SCHEMA_PATH=/app/schemas/stock-alerts.avsc`
- **보안**: `ALERT_SERVICE_JWT_SECRET` (개발 환경에서는 compose에 정의된 기본값 사용)

## 데이터베이스 마이그레이션
컨테이너 시작 시 `alembic upgrade head`가 자동으로 실행됩니다. 별도의 수동 마이그레이션 단계가 필요하지 않습니다. (현재 Head: `0001_initial`)

## 배포 및 실행
```bash
docker compose -f docker-compose.dev.yml up -d alert_service
```

## 상태 확인 (Health Check)
```bash
curl -fsS http://localhost:8000/health
```
정상 응답: `{"status":"ok"}`

## 빌드 참고
- **Run-from-source**: `PYTHONPATH=/app/services/alert_service/src` 환경에서 실행됩니다.
- **Apple Silicon (arm64)**: `cryptography` 라이브러리 호환성을 위해 `ENV OPENSSL_armcap=0` 설정이 적용되어 있습니다.
