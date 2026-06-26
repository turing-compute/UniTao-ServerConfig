#!/bin/bash
# Netmaker first-boot initialization
# Generates new MasterKey and admin user

CONFIG=/etc/netmaker/config.json
DB=/data/netmaker.db

if [ -f /etc/netmaker/.initialized ]; then
    exit 0
fi

MK=$(python3 -c "import secrets,base64; print(base64.b64encode(secrets.token_bytes(32)).decode())")
ADMIN_PW=$(python3 -c "import secrets; print(secrets.token_urlsafe(12))")

python3 -c "
import json
with open('$CONFIG') as f:
    c = json.load(f)
c['server']['masterkey'] = '$MK'
with open('$CONFIG', 'w') as f:
    json.dump(c, f, indent=2)
"

systemctl restart netmaker
sleep 15

HASH=$(python3 -c "import bcrypt; print(bcrypt.hashpw(b'$ADMIN_PW', bcrypt.gensalt()).decode())")
UID=$(python3 -c "import uuid; print(str(uuid.uuid4()))")
sqlite3 $DB "INSERT INTO users_v1 (id, username, password, platform_role_id, created_at, updated_at) VALUES ('$UID', 'admin', '$HASH', 'super-admin', datetime('now','utc'), datetime('now','utc'));"

cat > /etc/netmaker/credentials.txt <<CRED
MasterKey: $MK
Admin: admin / $ADMIN_PW
CRED
chmod 600 /etc/netmaker/credentials.txt
echo "Netmaker initialized" > /etc/netmaker/.initialized
echo "MasterKey: $MK"
echo "Admin password: $ADMIN_PW"
