# frontend/Dockerfile

# Stage 1: Build the React application
FROM node:20-alpine as builder

WORKDIR /app

# Copy dependency manifests first (better layer caching)
COPY package.json package-lock.json ./

# Install dependencies
RUN npm ci

# Copy the rest of the application source code (this includes ./frontend/.env)
COPY . .

# Build the production-ready static files
# Vite automatically loads ./frontend/.env (and .env.*) at build time
RUN npm run build

# Stage 2: Serve the static files with Nginx
FROM nginx:stable-alpine as final

# Copy the built static files into Nginx's web root
COPY --from=builder /app/dist /usr/share/nginx/html

# Copy the custom Nginx configuration with proxy settings
COPY nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]
