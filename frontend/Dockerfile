# syntax=docker/dockerfile:1

FROM node:20-alpine AS build

WORKDIR /app

# vite + @sveltejs/vite-plugin-svelte are devDependencies needed to build: do NOT --omit=dev.
COPY package.json package-lock.json ./
RUN npm ci

# .dockerignore excludes .env*, so VITE_DEV_JWT (in .env.local) is absent and never baked in.
COPY . .
RUN npm run build

FROM nginx:alpine AS serve

# Non-root + readOnlyRootFilesystem: pid/temp under writable /tmp, logs via base-image stdout/stderr symlinks.
RUN cat > /etc/nginx/nginx.conf <<'NGINX'
worker_processes  auto;
error_log  /var/log/nginx/error.log notice;
pid        /tmp/nginx.pid;

events {
    worker_connections  1024;
}

http {
    include       /etc/nginx/mime.types;
    default_type  application/octet-stream;

    access_log  /var/log/nginx/access.log;

    sendfile           on;
    keepalive_timeout  65;

    client_body_temp_path  /tmp/nginx-client-body;
    proxy_temp_path        /tmp/nginx-proxy;
    fastcgi_temp_path      /tmp/nginx-fastcgi;
    uwsgi_temp_path        /tmp/nginx-uwsgi;
    scgi_temp_path         /tmp/nginx-scgi;

    include /etc/nginx/conf.d/*.conf;
}
NGINX

RUN cat > /etc/nginx/conf.d/default.conf <<'NGINX'
server {
    # uid 65532 + drop ALL caps => cannot bind :80. Listen on high port 8080.
    listen       8080;
    server_name  _;

    root   /usr/share/nginx/html;
    index  index.html;

    # CoreDNS ClusterIP (k3s default): resolve the backend per-request so nginx boots even if it is down.
    resolver 10.43.0.10 valid=30s;
    set $alert_backend alert-service.invest.svc.cluster.local;

    location /api/ {
        proxy_pass http://$alert_backend:8000;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    location /ws {
        proxy_pass http://$alert_backend:8000;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 3600s;
    }

    location / {
        try_files $uri $uri/ /index.html;
    }
}
NGINX

COPY --from=build /app/dist /usr/share/nginx/html

EXPOSE 8080

CMD ["nginx", "-g", "daemon off;"]
