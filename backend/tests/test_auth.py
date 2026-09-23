from __future__ import annotations

import asyncio
from types import SimpleNamespace

from mia_dpp.api.auth import authenticated_user
from mia_dpp.config import Settings
from mia_dpp.persistence.catalogue import LOCAL_USER_ID


def test_local_auth_bypass_uses_shared_local_identity(tmp_path) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text('{"auth":{"enabled":false}}', encoding="utf-8")
    settings = Settings(
        _env_file=None,
        MIA_LOCAL_MODE=True,
        MIA_CONFIG_PATH=config_path,
        CLERK_SECRET_KEY="sk_test_configured",
    )
    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(mia=SimpleNamespace(settings=settings)))
    )

    user_id = asyncio.run(authenticated_user(request))

    assert user_id == LOCAL_USER_ID
