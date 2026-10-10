#!/bin/bash
# 单容器启动脚本：初始化 postgres（如需），然后启动 supervisord

set -e

# 初始化 postgres 数据目录（首次运行时）
if [ ! -f /var/lib/postgresql/data/PG_VERSION ]; then
    echo "初始化 postgres 数据目录..."
    su postgres -c "/usr/lib/postgresql/16/bin/initdb -D /var/lib/postgresql/data"
    # 创建用户和数据库
    su postgres -c "/usr/lib/postgresql/16/bin/pg_ctl -D /var/lib/postgresql/data -l /tmp/pg_init.log start"
    sleep 3
    su postgres -c "psql -c \"CREATE USER oci WITH PASSWORD 'oci' SUPERUSER;\""
    su postgres -c "psql -c \"CREATE DATABASE ocipanel OWNER oci;\""
    su postgres -c "/usr/lib/postgresql/16/bin/pg_ctl -D /var/lib/postgresql/data stop"
    echo "postgres 初始化完成"
fi

mkdir -p /data && chmod 777 /data
chown -R postgres:postgres /var/lib/postgresql /var/run/postgresql
# 修复 Alpine 旧数据的 locale 名称（en_US.utf8 -> C，避免 Debian 缺 locale 导致无法启动）
if [ -f /var/lib/postgresql/data/postgresql.conf ]; then
    sed -i "s/^lc_messages.*/lc_messages = 'C'/" /var/lib/postgresql/data/postgresql.conf
    sed -i "s/^lc_monetary.*/lc_monetary = 'C'/" /var/lib/postgresql/data/postgresql.conf
    sed -i "s/^lc_numeric.*/lc_numeric = 'C'/" /var/lib/postgresql/data/postgresql.conf
    sed -i "s/^lc_time.*/lc_time = 'C'/" /var/lib/postgresql/data/postgresql.conf
fi

# 单容器内：让 postgres/redis 主机名指向本机（兼容 .env 里的旧配置）
grep -q "postgres" /etc/hosts || echo "127.0.0.1 postgres" >> /etc/hosts
grep -q "redis" /etc/hosts || echo "127.0.0.1 redis" >> /etc/hosts

# 启动 supervisord
exec /usr/bin/supervisord -c /etc/supervisor/conf.d/supervisord.conf
