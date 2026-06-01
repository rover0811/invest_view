# Alert Service

Stock alerts serving service (Kafka consumer + WebSocket + REST API).

Kafka의 `stock-alerts` 토픽을 구독하여 수신된 알림 이벤트를 PostgreSQL(`alert_service.alert_events`)에 저장하고, FastAPI를 통해 외부 인터페이스를 제공하는 서비스입니다. kind 클러스터 위 Kubernetes Deployment로 실행됩니다.

## 환경 설정
상세 설정 항목은 `services/alert_service/.env.example`을 참조하십시오.

- **데이터베이스**: `ALERT_SERVICE_DATABASE_URL` (postgresql+asyncpg://...)
- **Kafka**: `ALERT_SERVICE_KAFKA_BOOTSTRAP_SERVERS=invest-kafka-kafka-bootstrap.kafka.svc:9092`, `ALERT_SERVICE_SCHEMA_REGISTRY_URL`
- **Avro**: `ALERT_SERVICE_AVRO_SCHEMA_PATH=/app/schemas/stock-alerts.avsc`
- **보안**: `ALERT_SERVICE_JWT_SECRET` (개발/kind 환경에서는 기본 Secret 값을 사용)

## 데이터베이스 마이그레이션
컨테이너 시작 시 `alembic upgrade head`가 자동으로 실행됩니다. 별도의 수동 마이그레이션 단계가 필요하지 않습니다. (현재 Head: `0001_initial`)

## 배포 및 실행
```bash
make images apps   # 이미지 빌드 + kind load + 배포 (alert_service 포함)
```

## 모니터링 및 상태 확인
```bash
kubectl logs -f deploy/alert-service
```

### 정상 상태 (Healthy)
로그에 다음과 같은 내용이 포함되어야 합니다:
- `alembic upgrade head` 실행 및 성공 메시지
- `uvicorn` 서버가 `0.0.0.0:8000`에서 시작됨
- Kafka 컨슈머가 `stock-alerts` 토픽에 성공적으로 할당됨

### 상태 확인 (Health Check)
```bash
curl -fsS http://localhost:8000/health
```
정상 응답: `{"status":"ok"}`

## 가격 서빙 API (Price Serving)

tick_persistence와 event_pattern_persistence가 적재한 데이터를 읽는 read-only API입니다.

| Endpoint | 설명 |
|---|---|
| `GET /api/candles/{symbol}?limit=200` | 5분봉 OHLC (`silver.symbol_5m_metrics`) |
| `GET /api/snapshot/{symbol}` | 종목 현재 상태 스냅샷 (`serving.symbol_snapshot`) |
| `GET /api/timeline/{symbol}?limit=100` | 알림+패턴 통합 타임라인 (`serving.symbol_signal_timeline`) |

`time` 필드는 UTC epoch seconds로 반환되어 Lightweight Charts `UTCTimestamp`와 호환됩니다.

## 재시작 정책 (Restart Behavior)
Kubernetes Deployment로 운영되며, `restartPolicy: Always`(기본값)가 적용됩니다. 컨슈머 태스크가 치명적 오류로 종료되면 `__main__.py`의 supervisor가 프로세스를 non-zero exit으로 종료하고, Kubernetes가 Pod를 재시작합니다.

## 빌드 참고
- **Run-from-source**: `PYTHONPATH=/app/services/alert_service/src` 환경에서 실행됩니다.
- **Apple Silicon (arm64)**: `cryptography` 라이브러리 호환성을 위해 `ENV OPENSSL_armcap=0` 설정이 적용되어 있습니다.
