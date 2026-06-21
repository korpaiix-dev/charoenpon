FROM node:20-alpine

WORKDIR /app

# Install deps first (better Docker cache)
COPY package.json ./
RUN npm install --omit=dev

# Copy source
COPY index.js ./

# Ensure /data exists for log
RUN mkdir -p /data

# Healthcheck via Express endpoint
HEALTHCHECK --interval=60s --timeout=5s --retries=3 \
  CMD wget -qO- http://localhost:3010/ | grep -q "alive" || exit 1

EXPOSE 3010

CMD ["node", "index.js"]
