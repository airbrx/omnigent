#!/bin/bash
# omnigent dev box bootstrap. Fails loudly: any error aborts and is visible in
# /var/log/user-data.log (and `cloud-init status --long`).
set -euxo pipefail
exec > >(tee -a /var/log/user-data.log) 2>&1

DEV_USER=michael

echo "=== [1/8] hostname + base packages ==="
hostnamectl set-hostname omnigent-devbox
export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y --no-install-recommends \
  ca-certificates curl wget gnupg jq unzip zip git tmux ripgrep fd-find \
  build-essential pkg-config libssl-dev python3 python3-venv python3-pip \
  htop tree less rsync openssh-client

echo "=== [2/8] 4GB swap (absorbs node/vite build spikes on a 4GB box) ==="
if [ ! -f /swapfile ]; then
  fallocate -l 4G /swapfile
  chmod 600 /swapfile
  mkswap /swapfile
  swapon /swapfile
  echo '/swapfile none swap sw 0 0' >> /etc/fstab
  # Prefer RAM; only reach for swap under real pressure.
  sysctl -w vm.swappiness=10
  echo 'vm.swappiness=10' > /etc/sysctl.d/99-swappiness.conf
fi

echo "=== [3/8] SSM agent ==="
snap list amazon-ssm-agent >/dev/null 2>&1 || snap install amazon-ssm-agent --classic
snap start amazon-ssm-agent || true
systemctl enable --now snap.amazon-ssm-agent.amazon-ssm-agent.service || true

echo "=== [4/8] user ${DEV_USER} ==="
if ! id -u "${DEV_USER}" >/dev/null 2>&1; then
  useradd -m -s /bin/bash -G sudo "${DEV_USER}"
fi
echo "${DEV_USER} ALL=(ALL) NOPASSWD:ALL" > "/etc/sudoers.d/90-${DEV_USER}"
chmod 440 "/etc/sudoers.d/90-${DEV_USER}"
# Keep the user's systemd --user session alive after logout so the omnigent
# host service survives disconnect (what `omnigent host install` relies on).
loginctl enable-linger "${DEV_USER}"

echo "=== [5/8] Node 22 (Claude Code runtime) ==="
curl -fsSL https://deb.nodesource.com/setup_22.x | bash -
apt-get install -y nodejs
node --version
npm --version

echo "=== [6/8] gh CLI ==="
mkdir -p -m 755 /etc/apt/keyrings
curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg \
  -o /etc/apt/keyrings/githubcli-archive-keyring.gpg
chmod go+r /etc/apt/keyrings/githubcli-archive-keyring.gpg
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" \
  > /etc/apt/sources.list.d/github-cli.list
apt-get update -y
apt-get install -y gh

echo "=== [7/8] AWS CLI v2 ==="
cd /tmp
curl -fsSL "https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip" -o awscliv2.zip
unzip -q -o awscliv2.zip
./aws/install --update
/usr/local/bin/aws --version
rm -rf /tmp/aws /tmp/awscliv2.zip

echo "=== [8/8] per-user toolchain: uv, omnigent, claude code ==="
# npm global dir owned by the user, so `npm i -g` needs no sudo later.
runuser -l "${DEV_USER}" -c 'mkdir -p ~/.npm-global && npm config set prefix ~/.npm-global'
runuser -l "${DEV_USER}" -c 'grep -q ".npm-global/bin" ~/.bashrc || echo "export PATH=\$HOME/.npm-global/bin:\$HOME/.local/bin:\$PATH" >> ~/.bashrc'
runuser -l "${DEV_USER}" -c 'curl -LsSf https://astral.sh/uv/install.sh | sh'
runuser -l "${DEV_USER}" -c 'export PATH=$HOME/.local/bin:$PATH && uv tool install --python 3.12 omnigent'
runuser -l "${DEV_USER}" -c 'export PATH=$HOME/.npm-global/bin:$PATH && npm install -g @anthropic-ai/claude-code'

echo "=== BOOTSTRAP COMPLETE ==="
runuser -l "${DEV_USER}" -c 'export PATH=$HOME/.npm-global/bin:$HOME/.local/bin:$PATH && echo "omnigent: $(command -v omnigent)" && echo "claude:   $(command -v claude)" && echo "gh:       $(command -v gh)"'
touch /var/lib/omnigent-devbox-bootstrapped
