"""Constants for the Samsung FamilyHub Fridge integration."""

DOMAIN = "samsung_familyhub_fridge"
CID = "5Hic3rk1FP"
DEFAULT_TIMEOUT = 10

# Config entry `data` keys
CONF_AUTH_MODE = "auth_mode"
CONF_TOKEN = "token"
CONF_DEVICE_ID = "device_id"

# Auth mode values
AUTH_MODE_PAT = "pat"                  # legacy: raw SmartThings Personal Access Token (24h, no camera)
AUTH_MODE_SAMSUNG = "samsung_account"  # Samsung Account email+password (camera-feed capable, auto-refresh)

# Samsung Account mode — config entry data keys
CONF_SAMSUNG_EMAIL = "samsung_email"
CONF_SAMSUNG_PASSWORD = "samsung_password"
CONF_SIGNIN_CLIENT_ID = "signin_client_id"
CONF_SIGNIN_CLIENT_SECRET = "signin_client_secret"
CONF_SAMSUNG_ACCESS_TOKEN = "samsung_access_token"
