FROM node:22-slim

WORKDIR /app

COPY package.json package-lock.json ./
RUN npm ci

COPY tsconfig.json types.d.ts ./
COPY *.ts ./

CMD ["npx", "tsx", "index.ts"]
