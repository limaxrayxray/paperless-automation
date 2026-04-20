#!/bin/bash
# ============================================================
# Création LXC Akaunting sur Proxmox
# À exécuter sur le HOST PVE (pas dans un LXC)
# Usage: bash create_akaunting_lxc.sh
# ============================================================
set -e

CT_ID=120
CT_IP=192.168.99.80
GW=192.168.99.1
TEMPLATE=debian-12-standard_12.7-1_amd64.tar.zst
DB_PASS="AkauntingDb2026!"

echo "=== Création LXC Akaunting (CT=$CT_ID, IP=$CT_IP) ==="

# ── Template Debian 12 ─────────────────────────────────────
echo "→ Vérification template..."
pveam update
if ! pveam list local 2>/dev/null | grep -q "$TEMPLATE"; then
    echo "→ Téléchargement $TEMPLATE..."
    pveam download local $TEMPLATE
fi

# ── Créer le conteneur ────────────────────────────────────
echo "→ Création du conteneur..."
pct create $CT_ID local:vztmpl/$TEMPLATE \
    --hostname akaunting \
    --cores 2 \
    --memory 2048 \
    --swap 512 \
    --rootfs local-lvm:20 \
    --net0 name=eth0,bridge=vmbr0,ip=${CT_IP}/24,gw=${GW} \
    --nameserver 8.8.8.8 \
    --start 1 \
    --unprivileged 1

echo "→ Attente démarrage conteneur..."
sleep 8

# ── Script d'installation (s'exécutera dans le LXC) ───────
cat > /tmp/akaunting_setup.sh << 'SETUP'
#!/bin/bash
set -e
export DEBIAN_FRONTEND=noninteractive
DB_PASS="AkauntingDb2026!"

echo ""
echo "[1/6] Packages système..."
apt-get update -qq
apt-get install -y curl gnupg2 lsb-release apt-transport-https ca-certificates unzip

echo "[2/6] PHP 8.2 + extensions..."
curl -fsSL https://packages.sury.org/php/apt.php | bash -
apt-get update -qq
apt-get install -y \
    php8.2 php8.2-fpm php8.2-mysql php8.2-xml php8.2-zip \
    php8.2-gd php8.2-curl php8.2-mbstring php8.2-bcmath \
    php8.2-intl php8.2-fileinfo php8.2-tokenizer php8.2-ctype \
    php8.2-dom php8.2-opcache

echo "[3/6] MariaDB + Nginx..."
apt-get install -y mariadb-server nginx
systemctl enable mariadb nginx php8.2-fpm
systemctl start mariadb

echo "[4/6] Base de données..."
mysql -e "CREATE DATABASE akaunting CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;"
mysql -e "CREATE USER 'akaunting'@'localhost' IDENTIFIED BY '${DB_PASS}';"
mysql -e "GRANT ALL PRIVILEGES ON akaunting.* TO 'akaunting'@'localhost'; FLUSH PRIVILEGES;"

echo "[5/6] Composer + Akaunting (peut prendre 2-3 min)..."
curl -sS https://getcomposer.org/installer | php -- --install-dir=/usr/local/bin --filename=composer
mkdir -p /var/www/akaunting
cd /var/www/akaunting
COMPOSER_ALLOW_SUPERUSER=1 composer create-project akaunting/akaunting . --no-interaction --quiet
chown -R www-data:www-data /var/www/akaunting
find /var/www/akaunting -type f -exec chmod 644 {} \;
find /var/www/akaunting -type d -exec chmod 755 {} \;
chmod -R 775 /var/www/akaunting/storage /var/www/akaunting/bootstrap/cache

echo "[6/6] Nginx..."
cat > /etc/nginx/sites-available/akaunting << 'NGINXEOF'
server {
    listen 80 default_server;
    server_name _;
    root /var/www/akaunting/public;
    index index.php;

    client_max_body_size 50M;

    location / {
        try_files $uri $uri/ /index.php?$query_string;
    }

    location ~ \.php$ {
        fastcgi_pass unix:/run/php/php8.2-fpm.sock;
        fastcgi_index index.php;
        fastcgi_param SCRIPT_FILENAME $realpath_root$fastcgi_script_name;
        include fastcgi_params;
        fastcgi_read_timeout 300;
    }

    location ~ /\.ht { deny all; }
}
NGINXEOF

ln -sf /etc/nginx/sites-available/akaunting /etc/nginx/sites-enabled/
rm -f /etc/nginx/sites-enabled/default
nginx -t && systemctl reload nginx

# Sauvegarde des infos de connexion DB
cat > /root/akaunting_info.txt << INFO
Akaunting DB Info
=================
Database: akaunting
User:     akaunting
Password: ${DB_PASS}
Host:     localhost

Setup web: remplir ces infos dans l'interface de configuration
INFO

echo ""
echo "============================================"
echo " ✓ Akaunting installé avec succès!"
echo "============================================"
echo " → Ouvrir dans le navigateur pour continuer:"
echo "   http://192.168.99.80"
echo ""
echo " → Config DB à saisir dans le wizard:"
echo "   Host:     localhost"
echo "   Database: akaunting"
echo "   User:     akaunting"
echo "   Password: ${DB_PASS}"
echo "============================================"
SETUP

# ── Pousser et exécuter dans le LXC ──────────────────────
echo "→ Lancement du setup dans le conteneur..."
pct push $CT_ID /tmp/akaunting_setup.sh /root/akaunting_setup.sh
pct exec $CT_ID -- bash /root/akaunting_setup.sh

echo ""
echo "=== TERMINÉ ==="
echo "→ Akaunting: http://$CT_IP"
echo "→ DB password: $DB_PASS"
