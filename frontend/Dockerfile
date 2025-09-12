# frontend/Dockerfile

# Stage 1: Build the React application
FROM node:20-alpine as builder

WORKDIR /app

# Copy dependency manifests
COPY package.json package-lock.json ./

# Install dependencies
RUN npm ci

# Copy the rest of the application source code
COPY . .

# Build the production-ready static files
RUN npm run build

# Stage 2: Serve the static files with Nginx
FROM nginx:stable-alpine as final

# Copy the built static files from the builder stage into the Nginx serve directory
COPY --from=builder /app/dist /usr/share/nginx/html

# Copy the custom Nginx configuration with proxy settings
COPY nginx.conf /etc/nginx/conf.d/default.conf

# Expose port 80 for the web server
EXPOSE 80

# The default Nginx command starts the server
CMD ["nginx", "-g", "daemon off;"]