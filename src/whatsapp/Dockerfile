FROM node:22-slim

WORKDIR /app

COPY src/whatsapp/package.json src/whatsapp/package-lock.json ./
RUN npm ci

COPY src/whatsapp/tsconfig.json src/whatsapp/types.d.ts ./
COPY src/whatsapp/*.ts ./

CMD ["npx", "tsx", "index.ts"]
