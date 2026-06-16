# Image de l'interface OpenCacao : nginx servant les fichiers statiques.
# Build depuis la RACINE du dépôt :
#   docker build -f web/Dockerfile -t <user>/opencacao-web:0.1.0 .
FROM nginx:1.27-alpine

# Fichiers de l'interface (index.html, src/, assets/…)
COPY web/ /usr/share/nginx/html/
# Config nginx + en-têtes de sécurité (OWASP)
COPY deploy/web.nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 80
