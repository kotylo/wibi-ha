"""Constants for the WiBi integration."""

from datetime import timedelta

DOMAIN = "wibi"

APPLICATION_TYPE = "Wibi"
API_BASE_URL = "https://api.schoolfox.com"
SSO_LOGIN_URL = (
    f"{API_BASE_URL}/api/users/login/meinwien?applicationType={APPLICATION_TYPE}"
)
SSO_CALLBACK_HOST = "wibi.wien.gv.at"
SSO_CALLBACK_PATH = "/sso-success"

CONF_AUTH = "auth"
CONF_CALLBACK_URL = "callback_url"

API_VERSION_HEADER = "ZUMO-API-VERSION"
API_VERSION = "2.0.0"
AUTH_HEADER = "X-ZUMO-AUTH"

TOKEN_REFRESH_INTERVAL = timedelta(hours=23)
