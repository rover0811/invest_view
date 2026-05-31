# Alert Service

Stock alerts serving service (Kafka consumer + WebSocket + REST API).

Kafka의 `stock-alerts` 토픽을 구독하여 수신된 알림 이벤트를 PostgreSQL(`alert_service.alert_events`)에 저장하고, FastAPI를 통해 외부 인터페이스를 제공하는 서비스입니다. Docker 데몬으로 실행됩니다.

## 환경 설정
상세 설정 항목은 `services/alert_service/.env.example`을 참조하십시오.

- **데이터베이스**: `ALERT_SERVICE_DATABASE_URL` (postgresql+asyncpg://...)
- **Kafka**: `ALERT_SERVICE_KAFKA_BOOTSTRAP_SERVERS=invest-kafka-kafka-bootstrap.kafka.svc:9092`, `ALERT_SERVICE_SCHEMA_REGISTRY_URL`
- **Avro**: `ALERT_SERVICE_AVRO_SCHEMA_PATH=/app/schemas/stock-alerts.avsc`
- **보안**: `ALERT_SERVICE_JWT_SECRET` (개발 환경에서는 compose에 정의된 기본값 사용)

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

## 재시작 정책 (Restart Behavior)
- `restart: unless-stopped` 정책을 사용합니다.
- **자동 재시작**: 치명적 오류로 프로세스가 비정상 종료(non-zero exit)되면 Docker가 자동으로 재시작합니다.
- **수동 중지**: `docker stop` 또는 `docker kill` 명령으로 중지된 경우에는 자동으로 재시작하지 않습니다. 이는 운영자의 의도를 존중하기 위함입니다. kill-also-restart가 필요하면 `restart: always`로 변경하십시오.
- **정상 종료**: SIGTERM 신호(예: `docker compose stop`)를 받으면 uvicorn이 graceful shutdown을 수행하고 exit 0으로 종료되며, 이 경우 재시작되지 않습니다.

## 빌드 참고
- **Run-from-source**: `PYTHONPATH=/app/services/alert_service/src` 환경에서 실행됩니다.
- **Apple Silicon (arm64)**: `cryptography` 라이브러리 호환성을 위해 `ENV OPENSSL_armcap=0` 설정이 적용되어 있습니다.
