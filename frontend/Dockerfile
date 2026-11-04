FROM node:20-alpine

WORKDIR /app

# Install dependencies first (cached layer)
COPY package*.json ./
RUN npm ci

# Copy source
COPY . .

EXPOSE 5173

# Dev: runs Vite dev server
CMD ["npm", "run", "dev", "--", "--host", "0.0.0.0"]

