# TODO(P6 - QA/Integration Lead): switch to a multi-stage build that runs
# `npm run build` and serves the static output (e.g. via nginx) before Day 6
# hardening — this dev-server stage is fine for Days 1-5 only.
FROM node:20-slim

WORKDIR /app
COPY package.json .
RUN npm install

COPY . .

EXPOSE 5173
CMD ["npm", "run", "dev", "--", "--host"]
