# ===================================================================
# 文件：frontend/Dockerfile（前端容器构建脚本）
# 用途：把单文件前端（index.html）打进轻量 nginx 镜像，随 docker compose 一起部署
# 产物：nginx 容器，80 端口，静态页面 + /api 反向代理到后端
# ===================================================================
FROM nginx:1.27-alpine  # 基础镜像：官方 nginx 1.27（alpine 版，体积仅约 20MB）
COPY nginx.conf /etc/nginx/conf.d/default.conf  # 覆盖默认站点配置：注入 /api 反代规则
COPY index.html /usr/share/nginx/html/index.html  # 把单文件前端放进 nginx 默认静态目录
EXPOSE 80  # 声明容器监听 80 端口（compose 端口映射以此为准）
