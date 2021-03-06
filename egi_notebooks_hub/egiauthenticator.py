"""EGI Check-in authenticator for JupyterHub

Uses OpenID Connect with aai.egi.eu
"""


import json
import os
import time

from oauthenticator.generic import GenericOAuthenticator
from tornado.httpclient import (
    AsyncHTTPClient,
    HTTPClientError,
    HTTPError,
    HTTPRequest,
)
from tornado.httputil import url_concat
from traitlets import Bool, List, Unicode, default, validate


class EGICheckinAuthenticator(GenericOAuthenticator):
    login_service = "EGI Check-in"

    checkin_host_env = "EGICHECKIN_HOST"
    checkin_host = Unicode(config=True, help="""The EGI Check-in host to use""")

    @default("checkin_host")
    def _checkin_host_default(self):
        default = "aai.egi.eu"
        if self.checkin_host_env:
            return os.getenv(self.checkin_host_env, default)
        return default

    @default("authorize_url")
    def _authorize_url_default(self):
        return "https://%s/oidc/authorize" % self.checkin_host

    @default("token_url")
    def _token_url_default(self):
        return "https://%s/oidc/token" % self.checkin_host

    @default("userdata_url")
    def _userdata_url_default(self):
        return "https://%s/oidc/userinfo" % self.checkin_host

    client_id_env = "EGICHECKIN_CLIENT_ID"
    client_secret_env = "EGICHECKIN_CLIENT_SECRET"

    scope = List(
        Unicode(),
        default_value=[
            "openid",
            "profile",
            "eduperson_scoped_affiliation",
            "eduperson_entitlement",
            "offline_access",
        ],
        config=True,
        help="""The OAuth scopes to request.

        See https://wiki.egi.eu/wiki/AAI_guide_for_SPs
        #OpenID_Connect_Service_Provider for details.

        At least 'openid' is required.
        """,
    )

    @validate("scope")
    def _validate_scope(self, proposal):
        """ensure openid is requested"""
        if "openid" not in proposal.value:
            return ["openid"] + proposal.value
        return proposal.value

    #  User name in Check-in comes in sub, but we are defaulting to
    # preferred_username as sub is too long to be used as id for
    # volumes
    username_key = Unicode(
        "preferred_username",
        config=True,
        help="""
        Claim name to use for getting the user name. 'sub' is unique but it's
        too long.
        """,
    )

    # Refresh auth data for user
    async def refresh_user(self, user, handler=None):
        auth_state = await user.get_auth_state()
        if not auth_state or "refresh_token" not in auth_state:
            self.log.warning("Cannot refresh user info without refresh token")
            return False

        now = time.time()
        refresh_info = auth_state.get("refresh_info", {})
        # if the token is still valid, avoid refreshing
        time_left = refresh_info.get("expiry_time", 0) - now
        if time_left > self.auth_refresh_age:
            self.log.debug("Credentials still valid, time left: %f", time_left)
            return True

        # performing the refresh token call
        self.log.debug("Perform refresh call to Check-in")
        http_client = AsyncHTTPClient()
        headers = {
            "Accept": "application/json",
            "User-Agent": "JupyterHub",
        }
        params = dict(
            client_id=self.client_id,
            client_secret=self.client_secret,
            grant_type="refresh_token",
            refresh_token=auth_state["refresh_token"],
            scope=" ".join(self.scope),
        )
        url = url_concat(self.token_url, params)
        req = HTTPRequest(
            url,
            auth_username=self.client_id,
            auth_password=self.client_secret,
            headers=headers,
            method="POST",
            body="",
        )
        try:
            resp = await http_client.fetch(req)
        except HTTPClientError as e:
            self.log.warning("Unable to refresh token, maybe expired: %s", e)
            return False
        refresh_info = json.loads(resp.body.decode("utf8", "replace"))
        refresh_info["expiry_time"] = now + refresh_info["expires_in"]
        auth_state["refresh_info"] = refresh_info
        auth_state["access_token"] = refresh_info["access_token"]
        auth_state["refresh_token"] = refresh_info["refresh_token"]
        self.log.debug("Refreshed token for user!")
        if callable(getattr(user.spawner, "set_access_token", None)):
            user.spawner.set_access_token(
                auth_state["access_token"], refresh_info.get("id_token", None)
            )
        return {"auth_state": auth_state}

    async def pre_spawn_start(self, user, spawner):
        auth_state = await user.get_auth_state()
        if auth_state and callable(getattr(user.spawner, "set_access_token", None)):
            user.spawner.set_access_token(auth_state["access_token"])


class DataHubAuthenticator(EGICheckinAuthenticator):
    """
    EGI Check-in + datahub authenticator for JupyterHub
    Uses OpenID Connect with aai.egi.eu, fetches DataHub token
    and keeps it in auth_state
    """

    onezone_env = Unicode(
        "ONEZONE_URL",
        config=True,
        help="""Environment variable that contains the onezone URL""",
    )

    token_env = Unicode(
        "ONECLIENT_ACCESS_TOKEN",
        config=True,
        help="""Environment variable that contains the access token
                for DataHub""",
    )

    oneprovider_env = Unicode(
        "ONEPROVIDER_HOST",
        config=True,
        help="""Environment variable that contains the oneprovider host""",
    )

    onezone_url = Unicode(
        "https://datahub.egi.eu", config=True, help="""Onedata onezone URL"""
    )

    oneprovider_host = Unicode(
        "plg-cyfronet-01.datahub.egi.eu",
        config=True,
        help="""Onedata oneprovider hostname""",
    )

    onepanel_url = Unicode(
        "",
        config=True,
        help="""Endpoint of the oneprovider to establish mappings,
                if undefined, it will use https://<oneprovider_host>:9443/""",
    )

    oneprovider_token = Unicode("", config=True, help="""Onedata oneprovider token""")

    map_users = Bool(False, config=True, help="""perform mapping""")

    token_name = Unicode(
        "notebooks.egi.eu",
        config=True,
        help="""Name of token in the onezone for the user""",
    )

    storage_id = Unicode("", config=True, help="""Storage id to use for mapping""")

    async def authenticate(self, handler, data=None):
        user_data = await super(DataHubAuthenticator, self).authenticate(handler, data)
        http_client = AsyncHTTPClient()
        onedata_token = None
        onedata_user = None
        # We now go to the datahub to get a token
        checkin_token = user_data["auth_state"]["access_token"]
        headers = {
            "content-type": "application/json",
            "x-auth-token": "egi:%s" % checkin_token,
        }
        token_url = (
            self.onezone_url
            + "/api/v3/onezone/user/tokens/named/name/%s" % self.token_name
        )
        req = HTTPRequest(token_url, headers=headers, method="GET")
        try:
            resp = await http_client.fetch(req)
            datahub_response = json.loads(resp.body.decode("utf8", "replace"))
            onedata_token = datahub_response["token"]
            onedata_user = datahub_response["subject"]["id"]
            self.log.debug("Reusing existing token!")
        except HTTPError as e:
            if e.code != 404:
                self.log.info("Something failed! %s", e)
                raise e
        if not onedata_token:
            # we don't have a token, create one
            token_desc = {
                "name": self.token_name,
                "type": {"accessToken": {}},
                "caveats": [
                    {"type": "interface", "interface": "oneclient"},
                ],
            }
            req = HTTPRequest(
                self.onezone_url + "/api/v3/onezone/user/tokens/named",
                headers=headers,
                method="POST",
                body=json.dumps(token_desc),
            )
            try:
                resp = await http_client.fetch(req)
                datahub_response = json.loads(resp.body.decode("utf8", "replace"))
                onedata_token = datahub_response["token"]
            except HTTPError as e:
                self.log.info("Something failed! %s", e)
                raise e
            # Finally get the user information
            req = HTTPRequest(
                self.onezone_url + "/api/v3/onezone/user",
                headers=headers,
                method="GET",
            )
            try:
                resp = await http_client.fetch(req)
                datahub_response = json.loads(resp.body.decode("utf8", "replace"))
                onedata_user = datahub_response["userId"]
            except HTTPError as e:
                self.log.info("Something failed! %s", e)
                raise e
        user_data["auth_state"].update(
            {"onedata_token": onedata_token, "onedata_user": onedata_user}
        )
        return user_data

    async def pre_spawn_start(self, user, spawner):
        await super(DataHubAuthenticator, self).pre_spawn_start(user, spawner)
        auth_state = await user.get_auth_state()
        if not auth_state:
            # auth_state not enabled
            return
        if self.map_users:
            if self.onepanel_url:
                map_url = self.onepanel_url
            else:
                map_url = f"https://{self.oneprovider_host}:9443"
            map_url += (
                f"/api/v3/onepanel/provider/storages/{self.storage_id}"
                "/luma/local_feed/storage_access/all"
                "/onedata_user_to_credentials"
            )
            headers = {
                "content-type": "application/json",
                "x-auth-token": self.oneprovider_token,
            }
            http_client = AsyncHTTPClient()
            user_id = auth_state.get("onedata_user")
            req = HTTPRequest(map_url + f"/{user_id}", headers=headers, method="GET")
            try:
                resp = await http_client.fetch(req)
                self.log.info("Mapping exists: %s", resp.body)
            except HTTPError as e:
                if e.code == 404:
                    mapping = {
                        "onedataUser": {
                            "mappingScheme": "onedataUser",
                            "onedataUserId": user_id,
                        },
                        "storageUser": {
                            "storageCredentials": {"type": "posix", "uid": "1000"},
                            "displayUid": "1000",
                        },
                    }
                    req = HTTPRequest(
                        map_url,
                        headers=headers,
                        method="POST",
                        body=json.dumps(mapping),
                    )
                    try:
                        resp = await http_client.fetch(req)
                        self.log.info("Mapping created: %s", resp.body)
                    except HTTPError as e:
                        self.log.info("Something failed! %s", e)
                        raise e
                else:
                    self.log.info("Something failed! %s", e)
                    raise e
        spawner.environment[self.token_env] = auth_state.get("onedata_token")
        spawner.environment[self.oneprovider_env] = self.oneprovider_host
