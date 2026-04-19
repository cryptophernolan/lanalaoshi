# Build React dashboard và đóng gói vào nginx image
# Không cần container riêng cho dashboard nữa

# Stage 1: Build
FROM node:20-alpine AS builder
WORKDIR /app
COPY dashboard/package*.json ./
RUN npm ci
COPY dashboard/ .
RUN VITE_API_BASE="" VITE_WS_URL="" npm run build

# Stage 2: Nginx serve
FROM nginx:alpine
COPY --from=builder /app/dist /usr/share/nginx/html
# nginx.conf được mount từ docker-compose, không COPY ở đây
EXPOSE 80
