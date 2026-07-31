#!/usr/bin/env bash
set -euo pipefail

domain=shiroha-rin.world
hostname=smtp.${domain}
selector=mail2026
key_dir=/etc/opendkim/keys/${domain}

export DEBIAN_FRONTEND=noninteractive
debconf-set-selections <<EOF
postfix postfix/mailname string ${domain}
postfix postfix/main_mailer_type select No configuration
EOF
apt-get update
apt-get install -y postfix opendkim opendkim-tools

if [ ! -f /etc/postfix/main.cf ]; then
  cp /usr/share/postfix/main.cf.debian /etc/postfix/main.cf
fi

printf '%s\n' "${domain}" > /etc/mailname
postconf -e "myhostname = ${hostname}"
postconf -e "mydomain = ${domain}"
postconf -e 'myorigin = $mydomain'
postconf -e 'inet_interfaces = loopback-only'
postconf -e 'inet_protocols = all'
postconf -e 'mydestination ='
postconf -e 'mynetworks = 127.0.0.0/8 [::1]/128'
postconf -e 'smtpd_relay_restrictions = permit_mynetworks, reject_unauth_destination'
postconf -e 'smtpd_tls_security_level = may'
postconf -e 'smtp_tls_security_level = may'
postconf -e 'milter_protocol = 6'
postconf -e 'milter_default_action = accept'
postconf -e 'smtpd_milters = inet:127.0.0.1:8891'
postconf -e 'non_smtpd_milters = inet:127.0.0.1:8891'

install -d -o opendkim -g opendkim -m 0750 "${key_dir}"
if [ ! -f "${key_dir}/${selector}.private" ]; then
  opendkim-genkey -b 2048 -D "${key_dir}" -d "${domain}" -s "${selector}"
  chown opendkim:opendkim "${key_dir}/${selector}.private"
  chmod 0600 "${key_dir}/${selector}.private"
fi

cat > /etc/opendkim.conf <<EOF
Syslog                  yes
UMask                   007
Mode                    sv
Canonicalization        relaxed/simple
OversignHeaders         From
Socket                  inet:8891@127.0.0.1
PidFile                 /run/opendkim/opendkim.pid
UserID                  opendkim
KeyTable                /etc/opendkim/key.table
SigningTable            refile:/etc/opendkim/signing.table
ExternalIgnoreList      refile:/etc/opendkim/trusted.hosts
InternalHosts           refile:/etc/opendkim/trusted.hosts
EOF

cat > /etc/opendkim/key.table <<EOF
${selector}._domainkey.${domain} ${domain}:${selector}:${key_dir}/${selector}.private
EOF
cat > /etc/opendkim/signing.table <<EOF
*@${domain} ${selector}._domainkey.${domain}
EOF
cat > /etc/opendkim/trusted.hosts <<EOF
127.0.0.1
::1
localhost
${hostname}
EOF
chmod 0644 /etc/opendkim/key.table /etc/opendkim/signing.table /etc/opendkim/trusted.hosts /etc/opendkim.conf

systemctl enable --now opendkim postfix
postfix check
opendkim-testkey -d "${domain}" -s "${selector}" -k "${key_dir}/${selector}.private" -vvv || true
printf 'DKIM_RECORD\n'
tr -d '\n' < "${key_dir}/${selector}.txt"
printf '\n'
