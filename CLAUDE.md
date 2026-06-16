# CLAUDE.md — invest_view 함정 노트

운영은 homelab k3s에서 돈다. 아래는 **모르면 반드시 한 번 삽질하는 것들**만 모았다. 일반 절차는 README 참고.

## 접속

- SSH는 `ssh hyunsoo-cluster1` (user `hyunsoo`, Tailscale `100.89.9.31`). **`ssh homelab`은 alias가 없어서 실패한다.**
- 키 인증이라 비밀번호 불필요. tmux 쓰지 말고 `ssh hyunsoo-cluster1 '<cmd>'`로 직접 실행.

## 클러스터

- **k3s다 (docker 아님).** `sudo k3s kubectl ...`로 실행 (kubeconfig가 root 소유, sudo는 NOPASSWD).
- **맥북 `~/.kube/config`는 prod가 아니다** (kind context). prod 조작은 homelab에서만.
- 서비스 = `invest` ns, Postgres = `postgres` ns.

## 배포 (이게 가장 많이 헷갈림)

- **이 레포를 머지해도 배포 안 된다.** Flux가 보는 건 **별도 레포** `rover0811/homelab-infra` (로컬 `~/AnyProjects/homelab-infra`).
- kis-ingestion 매니페스트: `homelab-infra/.../invest/services/deployments.yaml`.
- **이미지 자동화/CI 없음.** 빌드·태그 변경 전부 수동:
  1. **이미지는 맥북에서 빌드한다 (homelab엔 docker가 없다).** prod 노드는 amd64인데 맥북은 arm64 → buildx 크로스빌드 필수:
     ```bash
     gh auth token | docker login ghcr.io -u rover0811 --password-stdin
     TAG=$(git rev-parse --short HEAD)   # 태그 컨벤션 = 커밋해시
     docker buildx build --builder omo-amd64 --platform linux/amd64 \
       -f services/kis_ingestion/Dockerfile -t ghcr.io/rover0811/kis_ingestion:$TAG --push .
     ```
  2. `homelab-infra` 매니페스트의 이미지 태그를 `$TAG`로 바꿔 커밋·push → Flux가 배포.
- reconcile은 10분 주기(기다리면 됨). 급하면 `kustomization invest-services`에 `reconcile.fluxcd.io/requestedAt` annotate.

## DB

- **자격증명은 `postgres/postgres`가 아니다.** `invest` ns의 시크릿 `invest-db-credentials` (user `invest`, db `invest_view`):
  ```bash
  ssh hyunsoo-cluster1 'P=$(sudo k3s kubectl -n invest get secret invest-db-credentials -o jsonpath="{.data.POSTGRES_PASSWORD}" | base64 -d); \
    sudo k3s kubectl -n postgres exec statefulset/postgresql -- env PGPASSWORD=$P psql -U invest -d invest_view \
    -c "SELECT count(*), max(persisted_at) FROM bronze.tick_history;"'
  ```
- 데이터 유입 검증은 `bronze.tick_history.max(persisted_at)`. **시간 컬럼은 `persisted_at` (`ts_event` 아님).**
- **장중(평일 09:00~15:30 KST)에만 tick이 들어온다.** 장외/주말에 tick 0은 정상 (연결만 유지됨).

## 전원

- **켤 때:** 전원 버튼만 누르면 끝. k3s는 enabled라 자동 기동, 파드 자동 재생성, kis-ingestion 자동 재구독.
- **끌 때:** 반드시 `ssh hyunsoo-cluster1 'sudo shutdown -h now'`. 전원 강제 차단은 Postgres/Kafka 디스크 손상 위험.
- 데이터는 PVC(`local-path`)라 재부팅해도 보존된다.

## kis-ingestion 건드릴 때 절대 깨지 말 것

- WebSocket `ping_interval=20, ping_timeout=20`을 **`None`으로 되돌리지 말 것.** half-open 연결에서 `recv()`가 영구 hang → 파드는 Running인데 데이터만 조용히 끊긴다 (탐지 불가).
- 재연결 5회 실패 시 `ReconnectExhaustedError`로 프로세스가 죽고 k8s가 재시작한다 (fail-fast). **이 예외는 `ConnectionError`/`OSError`/`RuntimeError`를 상속하면 안 된다** — 상속하면 consume loop이 다시 잡아 fail-fast가 깨지고 무한 재연결로 돌아간다. `RESTARTS` 증가가 장애 신호.
- 롤링 업데이트 중 `ALREADY IN USE appkey`(OPSP8996)는 이전/새 파드가 같은 appkey로 잠깐 겹쳐 나는 것 → 이전 파드 종료되면 자동 해소. 패닉 금지.
