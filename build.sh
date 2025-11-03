#!/usr/bin/env bash
set -eux

# Install dependencies for Microsoft ODBC Driver 18
curl https://packages.microsoft.com/keys/microsoft.asc | apt-key add -
curl https://packages.microsoft.com/config/debian/12/prod.list > /etc/apt/sources.list.d/mssql-release.list
apt-get update
ACCEPT_EULA=Y apt-get install -y msodbcsql18 unixodbc-dev

# Then install Python deps
pip install -r requirements.txt
