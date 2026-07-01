# frontend/Dockerfile

# Stage 1: Build the React application
FROM node:20-alpine as builder

WORKDIR /app

# Copy dependency manifests first (better layer caching)
COPY package.json package-lock.json ./

# Install dependencies
RUN npm ci

# Copy the rest of the application source code.
# NOTE (hitlist #10, 2026-06-30): .env and .env.* are EXCLUDED by .dockerignore
# (only .env.example is copied). This is deliberate — secrets must NOT be baked
# into the image, and any VITE_-prefixed value Vite inlines becomes PUBLIC in the
# shipped JS bundle. Build-time config is injected via build args / the CI env,
# not a committed .env. Do not "fix" this by un-ignoring .env.
COPY . .

# Build the production-ready static files.
# Vite would load ./frontend/.env / .env.* if present, but they are excluded at
# build (see above), so no committed secret can leak into the bundle here.
RUN npm run build

# Stage 2: Serve the static files with Nginx
FROM nginx:stable-alpine as final

# Copy the built static files into Nginx's web root
COPY --from=builder /app/dist /usr/share/nginx/html

# Copy the custom Nginx configuration with proxy settings
COPY nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]
