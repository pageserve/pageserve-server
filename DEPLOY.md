# Đóng gói & deploy qua Docker Hub

Image `pageserve-server` đã **self-contained**: app, UI, `pageindex_src`, migrations đều nằm trong image. Sau khi push lên Docker Hub, máy deploy chỉ cần **`docker-compose.prod.yml` + `.env`** (không cần clone repo).

> Thay `DOCKER_USER` bằng username Docker Hub của bạn.

---

## 1. Build & push image (máy có source)

```bash
docker login

# Build, gắn 2 tag: version cụ thể + latest
docker build -t DOCKER_USER/pageserve-server:0.1.0 -t DOCKER_USER/pageserve-server:latest .

docker push DOCKER_USER/pageserve-server:0.1.0
docker push DOCKER_USER/pageserve-server:latest
```

**Multi-arch** (build trên Mac ARM nhưng deploy server Intel/amd64, hoặc ngược lại):
```bash
docker buildx create --use --name psb 2>/dev/null || docker buildx use psb
docker buildx build --platform linux/amd64,linux/arm64 \
  -t DOCKER_USER/pageserve-server:0.1.0 -t DOCKER_USER/pageserve-server:latest \
  --push .
```

> Mỗi lần sửa code → build tag version mới (vd `0.1.1`) rồi push. Docker Hub **cho phép đè `latest`** (khác PyPI), nhưng nên luôn có tag version cố định để rollback.

---

## 2. Deploy trên máy khác (KHÔNG cần repo)

Chỉ cần copy 2 file sang máy deploy: `docker-compose.prod.yml` và `.env`.

`.env` tối thiểu:
```env
IMAGE=DOCKER_USER/pageserve-server:0.1.0

POSTGRES_PASSWORD=doi-mat-khau-manh
LLM_BASE_URL=https://your-llm/v1
LLM_MODEL=qwen3.5-9b
ADMIN_EMAIL=admin@company.com
ADMIN_PASSWORD=doi-ngay
JWT_SECRET=<openssl rand -hex 32>

PORT=8000
# tùy chọn: WORKER_JOB_TIMEOUT=3600  PAGEINDEX_NODE_SUMMARY=no ...
```

Chạy:
```bash
docker compose -f docker-compose.prod.yml pull      # kéo image từ Docker Hub
docker compose -f docker-compose.prod.yml up -d
docker compose -f docker-compose.prod.yml logs -f pageserve worker
```

Mở `http://<host>:<PORT>/ui/login.html` · health: `curl http://<host>:<PORT>/health`.

---

## 3. Cập nhật phiên bản mới

Trên máy build: build + push tag mới. Trên máy deploy:
```bash
# sửa IMAGE=DOCKER_USER/pageserve-server:0.1.1 trong .env (hoặc giữ :latest)
docker compose -f docker-compose.prod.yml pull
docker compose -f docker-compose.prod.yml up -d
```
Dữ liệu (Postgres/Redis/PDF) nằm trong volume nên **được giữ nguyên** khi đổi image. Migration tự chạy lúc API khởi động.

---

## Khác biệt với `docker-compose.yml` (dev)
| | dev (`docker-compose.yml`) | prod (`docker-compose.prod.yml`) |
|---|---|---|
| Nguồn image | `build: .` (cần source) | `image: $IMAGE` (kéo từ Docker Hub) |
| Bind-mount `./ui` | Có (sửa UI khỏi rebuild) | **Không** (UI đã trong image) |
| Dùng khi | phát triển | triển khai/máy khác |

## Lưu ý
- File PDF & data ở Docker volume (`pdf_files`, `postgres_data`, `redis_data`) — backup bằng `backup.sh`.
- `LLM_BASE_URL` phải truy cập được từ trong container (nếu LLM chạy trên host: dùng `host.docker.internal` + `extra_hosts: ["host.docker.internal:host-gateway"]`, hoặc IP host, hoặc URL public/ngrok).
- Endpoint `/files/{id}.pdf` hiện công khai theo UUID — cân nhắc đặt sau reverse proxy nếu cần.
