import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from sqlalchemy.exc import SQLAlchemyError

import manage


@pytest.mark.parametrize("fail", [False, True])
def test_seed_disposes_its_engine_after_success_or_failure(monkeypatch, capsys, fail):
    mysql = object()
    engine = Mock()
    load_config = Mock(return_value=SimpleNamespace(mysql=mysql))
    create_engine = Mock(return_value=engine)
    service = Mock()
    seed_service = Mock(return_value=service)
    if fail:
        service.seed.side_effect = SQLAlchemyError("private database error")
    monkeypatch.setattr(sys, "argv", ["manage.py", "seed"])
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
        assert capsys.readouterr().out == "seed completed\n"

    load_config.assert_called_once_with()
    create_engine.assert_called_once_with(mysql)
    seed_service.assert_called_once_with(engine)
    service.seed.assert_called_once_with()
    engine.dispose.assert_called_once_with()


def test_init_uses_the_independent_administration_connection(monkeypatch, capsys):
    mysql = object()
    initialize = Mock()
    create_engine = Mock()
    monkeypatch.setattr(sys, "argv", ["manage.py", "init"])
    monkeypatch.setattr(manage, "load_config", lambda: SimpleNamespace(mysql=mysql))
    monkeypatch.setattr(manage, "initialize_demo_database", initialize)
    monkeypatch.setattr(manage, "create_mysql_engine", create_engine)

    manage.main()
    assert capsys.readouterr().out == "init completed\n"
    initialize.assert_called_once_with(mysql)
    create_engine.assert_not_called()
