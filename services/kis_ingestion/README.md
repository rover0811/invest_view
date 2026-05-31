# KIS Ingestion Service

KIS Open API WebSocket 실시간 시세를 수집하여 Kafka(`stock-ticks` 토픽)로 발행하는 생산자 서비스입니다. Docker 데몬으로 실행됩니다.

## 주요 기능
- KIS Open API WebSocket 연결 및 실시간 체결가 수집
- 수집된 데이터를 Avro 포맷으로 직렬화하여 Kafka 발행
- 최대 40종목 실시간 구독 지원 (KIS 제한 사항)

## 환경 설정
상세 설정 항목은 `services/kis_ingestion/.env.example`을 참조하십시오.

- **인증**: `KIS_APP_KEY`, `KIS_APP_SECRET`은 루트 `.env` 파일에서 관리되며 Docker Compose를 통해 주입됩니다.
- **Kafka**: `KIS_KAFKA_ENABLED=true` 설정이 필수입니다.
- **주소**: Compose 환경 내에서 `kafka:29092`, `schema-registry:8081`을 사용합니다.
- **구독**: `KIS_SUBSCRIPTION_CAP=40`으로 제한되어 있으며, `KIS_WATCH_SYMBOLS`에 JSON 배열 문자열 형태로 대상 종목 코드를 설정합니다.

## 배포 및 실행
```bash
docker compose -f docker-compose.dev.yml up -d kis_ingestion
```

## 모니터링 및 상태 확인
```bash
docker compose -f docker-compose.dev.yml logs -f kis_ingestion
```

### 정상 상태 (Healthy Idle)
로그에 다음과 같은 내용이 포함되어야 합니다:
- `oauth2/Approval` POST 요청 결과 `200 OK`
- WebSocket 연결 성공
- 40개 종목 구독 완료
- `Kafka: enabled (broker=kafka:29092, topic=stock-ticks)` 메시지 출력

**참고**: 장 마감 후 또는 주말(20:00 KST 이후)에는 연결은 유지되나 실시간 체결 데이터(ticks)가 수신되지 않는 것이 정상입니다.

## 재시작 정책 (Restart Behavior)
- `restart: unless-stopped` 정책을 사용합니다.
- **자동 재시작**: 서비스 내부에서 치명적 오류(Fatal Error)가 발생하여 프로세스가 비정상 종료(non-zero exit)될 경우 Docker가 자동으로 재시작합니다.
- **수동 중지**: `docker stop` 또는 `docker kill` 명령으로 중지된 경우에는 자동으로 재시작되지 않습니다. 이는 운영자의 의도를 존중하기 위함입니다.

## 빌드 및 아키텍처 참고
- **Run-from-source**: 이미지는 소스 코드를 직접 실행하는 구조이며, `PYTHONPATH`는 `/app/services/kis_ingestion/src`로 설정됩니다.
- **Schema 경로**: Avro 스키마는 `/app/schemas/stock-ticks.avsc` 경로를 참조합니다.
- **Apple Silicon (arm64)**: `cryptography` 라이브러리의 SIGILL 오류 방지를 위해 `ENV OPENSSL_armcap=0` 설정이 포함되어 있습니다.
