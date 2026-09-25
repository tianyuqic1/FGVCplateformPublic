FROM node:22-slim

WORKDIR /app

COPY frontend/package*.json ./
RUN npm ci
RUN node -e "const fs=require('node:fs'),crypto=require('node:crypto');fs.writeFileSync('node_modules/.finevision-lock-sha256',crypto.createHash('sha256').update(fs.readFileSync('package-lock.json')).digest('hex')+'\n')"

COPY frontend ./

EXPOSE 5173

CMD ["node", "scripts/start-dev.mjs"]
