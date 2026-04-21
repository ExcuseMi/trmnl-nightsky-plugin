FROM node:24-alpine

WORKDIR /app

COPY package.json ./
RUN npm install --production

COPY run.js ./

# transform.js and test data are mounted at runtime — not baked in
# docker-compose mounts:
#   ./plugin/transform.js  → /app/transform.js
#   ./test/transform/data  → /app/data

CMD ["node", "run.js"]
