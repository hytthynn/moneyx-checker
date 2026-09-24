from services.jobs import DELIVERY_KEY_MAX_LENGTH, new_delivery_key


def test_manual_delivery_key_fits_supabase_column():
    key = new_delivery_key("manual")

    assert key.startswith("manual:")
    assert len(key) == 39
    assert len(key) <= DELIVERY_KEY_MAX_LENGTH


def test_each_cron_call_gets_a_new_delivery_key():
    first = new_delivery_key("cron")
    second = new_delivery_key("cron")

    assert first.startswith("cron:")
    assert len(first) == 37
    assert first != second
