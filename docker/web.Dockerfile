FROM node:26.12.1-alpine@sha256:40cd011b55415bc894e45b02fdd5a5dfde056921b7d23a64c74f80497af48797 AS builder

WORKDIR /app
RUN npm install --global yarn@1.22.22
COPY package.json yarn.lock ./
COPY frontend/package.json ./frontend/package.json
RUN yarn install --frozen-lockfile
COPY frontend ./frontend
RUN yarn workspace frontend build

FROM nginxinc/nginx-unprivileged:1.30.6-alpine@sha256:f5116ed4606f3eb015dd4a8e28cb9b351476dc0ad4bc6e9f93b19cec130cdf03 AS runtime

COPY --from=builder /app/frontend/dist /usr/share/nginx/html
COPY docker/nginx.conf /etc/nginx/conf.d/default.conf
COPY docker/nginx-headers.conf /etc/nginx/conf.d/nginx-headers.conf
USER 101
EXPOSE 8080
