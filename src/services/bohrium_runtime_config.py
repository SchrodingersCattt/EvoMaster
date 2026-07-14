"""Configuration shared by Bohrium agent runtime setup helpers."""

BOHRIUM_REMOTE_USER_SKILLS_ROOT = "/personal/.matmaster/skills"
BOHRIUM_REMOTE_USER_PLUGINS_ROOT = "/personal/.matmaster/plugins"

# Bash snippet for root on Bohrium SSH nodes: wget/curl/git/pip + env.
# GNU wget only accepts ``use_proxy = on|off``; ``use_proxy = no`` is invalid and
# leaves /etc/wgetrc proxy (e.g. ga.dp.tech:8118) in effect.
# Pip reads ``~/.pip/pip.conf`` / ``/etc/pip.conf`` ``[global] proxy=`` independently
# of shell env; strip those lines so ``pip install`` does not force ga.dp.tech.
CLEAR_REMOTE_PROXY_SCRIPT: str = (
    "rm -f /root/speedUp.sh /speedUp.sh; "
    "printf %s\\n "
    "'# matmaster-evo: disable platform proxy for OSS/outbound' "
    "'use_proxy = off' "
    "'proxy =' "
    "'http_proxy =' "
    "'https_proxy =' "
    "'ftp_proxy =' "
    "> /root/.wgetrc; "
    "printf %s\\n "
    "'# matmaster-evo: disable curl default proxy' "
    "'proxy = \"\"' "
    "'noproxy = \"*\"' "
    "> /root/.curlrc; "
    "git config --global --unset-all http.proxy 2>/dev/null; true; "
    "git config --global --unset-all https.proxy 2>/dev/null; true; "
    "[ -f /root/.pip/pip.conf ] && sed -i "
    "'/^[[:space:]]*proxy[[:space:]]*=/d' "
    "/root/.pip/pip.conf 2>/dev/null; true; "
    "[ -f /etc/pip.conf ] && sed -i "
    "'/^[[:space:]]*proxy[[:space:]]*=/d' "
    "/etc/pip.conf 2>/dev/null; true; "
    "export http_proxy= https_proxy= HTTP_PROXY= HTTPS_PROXY= ALL_PROXY= "
    "NO_PROXY= no_proxy= ftp_proxy= FTP_PROXY=; "
    "unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY ALL_PROXY "
    "NO_PROXY no_proxy ftp_proxy FTP_PROXY WGETRC 2>/dev/null; "
)
