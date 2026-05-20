# Stage 1 — build React
FROM node:20-alpine AS build-frontend
WORKDIR /app/frontend
COPY frontend/package*.json ./
RUN npm install
COPY frontend/ ./
RUN npm run build

# Stage 2 — Node API + serve static
FROM node:20-alpine
WORKDIR /app
COPY api/package*.json ./
RUN npm install --omit=dev
COPY api/ ./
COPY --from=build-frontend /app/frontend/dist ./public

ENV PORT=3001
ENV NODE_ENV=production
ENV INCIDENTS_DIR=/postmortems

EXPOSE 3001
CMD ["node", "server.js"]
