from sync.etf_sync.bootstrap import _command_state, _index_names


def test_command_state_accepts_strict_ejson_ok():
    assert _command_state({"list": [[{"ok": {"$numberInt": "1"}}]]}) == (True, [])


def test_command_state_reports_command_error():
    ok, errors = _command_state({"list": [[{"ok": {"$numberInt": "0"}, "code": "IndexOptionsConflict", "errmsg": "conflict"}]]})
    assert not ok
    assert errors[0]["code"] == "IndexOptionsConflict"


def test_index_names_reads_mongo_cursor_response():
    payload = {"list": [[{"cursor": {"firstBatch": [{"name": "_id_"}, {"name": "release_code"}]}, "ok": 1}]]}
    assert _index_names(payload) == ["_id_", "release_code"]
