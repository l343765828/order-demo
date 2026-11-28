import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from sqlalchemy.exc import SQLAlchemyError

import manage


@pytest.mark.parametrize("fail", [False, True])
@pytest.mark.parametrize("command, config_path", [("seed", None), ("reset", Path("development.json"))])
def test_data_commands_dispose_engine_after_success_or_failure(monkeypatch, capsys, fail, command, config_path):
    mysql = object()
    engine = Mock()
    load_config = Mock(return_value=SimpleNamespace(mysql=mysql))
    create_engine = Mock(return_value=engine)
    service = Mock()
    seed_service = Mock(return_value=service)
    operation = getattr(service, command)
    if fail:
        operation.side_effect = SQLAlchemyError("private database error")
    config_args = ["--config", str(config_path)] if config_path else []
    monkeypatch.setattr(sys, "argv", ["manage.py", *config_args, command])
    monkeypatch.setattr(manage, "load_config", load_config)
    monkeypatch.setattr(manage, "create_mysql_engine", create_engine)
    monkeypatch.setattr(manage, "MySQLSeedService", seed_service)

    if fail:
        with pytest.raises(SystemExit) as error:
            manage.main()
        assert error.value.code == 1
        output = capsys.readouterr()
        assert not output.out
        assert "private database error" not in output.err
    else:
        manage.main()
        assert capsys.readouterr().out == f"{command} completed\n"

    load_config.assert_called_once_with(config_path)
    create_engine.assert_called_once_with(mysql)
    seed_service.assert_called_once_with(engine)
    operation.assert_called_once_with()
    engine.dispose.assert_called_once_with()


def test_init_uses_the_independent_administration_connection(monkeypatch, capsys):
    mysql = object()
    initialize = Mock()
    create_engine = Mock()
    monkeypatch.setattr(sys, "argv", ["manage.py", "init"])
    monkeypatch.setattr(manage, "load_config", lambda _path: SimpleNamespace(mysql=mysql))
    monkeypatch.setattr(manage, "initialize_demo_database", initialize)
    monkeypatch.setattr(manage, "create_mysql_engine", create_engine)

    manage.main()
    assert capsys.readouterr().out == "init completed\n"
    initialize.assert_called_once_with(mysql)
    create_engine.assert_not_called()


def test_reset_requires_explicit_database_config(monkeypatch, capsys):
    load_config = Mock(side_effect=AssertionError('reset without --config must not load the default database'))
    monkeypatch.setattr(manage, 'load_config', load_config)
    monkeypatch.setattr(sys, 'argv', ['manage.py', 'reset'])

    with pytest.raises(SystemExit) as error:
        manage.main()

    assert error.value.code == 2
    assert '--config' in capsys.readouterr().err
