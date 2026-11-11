#!/usr/bin/env bash
# Installs Microsoft ODBC Driver 18 for SQL Server on Ubuntu (needed by pyodbc / SQLAlchemy).
# Safe to re-run: exits early if the driver is already registered.
set -euo pipefail

if grep -qs "ODBC Driver 18 for SQL Server" /etc/odbcinst.ini; then
  echo "ODBC Driver 18 for SQL Server already installed."
  exit 0
fi

UBUNTU_VERSION="$(. /etc/os-release && echo "$VERSION_ID")"
echo "Installing ODBC Driver 18 for Ubuntu ${UBUNTU_VERSION}..."

TMP_DEB="$(mktemp --suffix=.deb)"
curl -fsSL -o "$TMP_DEB" "https://packages.microsoft.com/config/ubuntu/${UBUNTU_VERSION}/packages-microsoft-prod.deb"
sudo dpkg -i "$TMP_DEB"
rm -f "$TMP_DEB"

sudo apt-get update
sudo ACCEPT_EULA=Y apt-get install -y msodbcsql18 unixodbc-dev

odbcinst -q -d -n "ODBC Driver 18 for SQL Server"
echo "Done."
