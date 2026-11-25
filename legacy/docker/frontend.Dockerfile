FROM node:22-alpine
WORKDIR /app
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
EXPOSE 5173
CMD ["sh", "-c", "npm run build && npm run preview -- --host 0.0.0.0 --port 5173"]
