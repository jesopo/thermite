from dataclasses import dataclass
from typing import Optional

import yaml


@dataclass
class Config(object):
    server_read: str
    password_read: Optional[str]
    server_write: str
    password_write: Optional[str]
    nickname: str
    channel: str

    db_user: str
    db_pass: Optional[str]
    db_host: Optional[str]
    db_name: str

    pipe_name: str
    make_pipe: str
    pinned_cert: Optional[str]


def load(filepath: str):
    with open(filepath) as file:
        config_yaml = yaml.safe_load(file.read())

    return Config(
        config_yaml["server-read"],
        config_yaml.get("password-read", None),
        config_yaml["server-write"],
        config_yaml.get("password-write", None),
        config_yaml["nickname"],
        config_yaml["channel"],
        config_yaml["database"]["user"],
        config_yaml["database"].get("pass", None),
        config_yaml["database"].get("host", None),
        config_yaml["database"]["name"],
        config_yaml["pipe-name"],
        config_yaml["make-pipe"],
        config_yaml.get("pinned-cert", None),
    )
