$ErrorActionPreference = "Stop"

Set-Location $PSScriptRoot

$env:DATABASE_URL = "sqlite:///dev.sqlite"
$env:UPLOAD_FOLDER = "app/static/uploads"
$env:SECRET_KEY = "local-dev-secret-key"
$env:TRUSTED_HOSTS = "localhost,127.0.0.1"

py app/app.py
