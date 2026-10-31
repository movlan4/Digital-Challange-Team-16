FROM nginx:1.27-alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY index.html passenger.html operator.html admin.html favicon.svg /usr/share/nginx/html/
COPY css /usr/share/nginx/html/css
COPY assets /usr/share/nginx/html/assets
COPY js /usr/share/nginx/html/js
EXPOSE 80
